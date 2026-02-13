"""
Docker image building for the automation workflow.

Handles:
- Building Docker images from repository
- Saving images to tar files
- Generating image URIs
"""

import logging
from pathlib import Path
from typing import Optional

from .config import PRInfo
from .utils import run_command


def check_docker_available(logger: logging.Logger) -> bool:
    """
    Check if Docker is available and running.

    Args:
        logger: Logger instance

    Returns:
        True if Docker is available
    """
    exit_code, stdout, stderr = run_command(
        ["docker", "version"],
        logger=logger,
        timeout=30
    )

    if exit_code != 0:
        logger.warning("Docker is not available or not running")
        return False

    logger.debug("Docker is available")
    return True


def create_minimal_dockerfile(repo_path: Path, language: str, logger: logging.Logger) -> Path:
    """
    Create a minimal Dockerfile if one doesn't exist.

    Args:
        repo_path: Path to repository
        language: Repository language
        logger: Logger instance

    Returns:
        Path to Dockerfile
    """
    dockerfile = repo_path / "Dockerfile"

    if dockerfile.exists():
        logger.info("Using existing Dockerfile")
        return dockerfile

    logger.info(f"Creating minimal Dockerfile for {language}")

    if language == "python":
        content = """FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt* ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

COPY setup.py pyproject.toml* ./
RUN pip install -e . 2>/dev/null || true

# Copy source
COPY . .

# Install test dependencies
RUN pip install pytest pytest-json-report

# Default command
CMD ["pytest", "-v"]
"""

    elif language in ("javascript", "typescript"):
        content = """FROM node:18-slim

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm install

# Copy source
COPY . .

# Default command
CMD ["npm", "test"]
"""

    elif language == "go":
        content = """FROM golang:1.21

WORKDIR /app

# Download dependencies
COPY go.mod go.sum* ./
RUN go mod download

# Copy source
COPY . .

# Default command
CMD ["go", "test", "./..."]
"""

    elif language == "rust":
        content = """FROM rust:1.70

WORKDIR /app

# Copy source
COPY . .

# Build
RUN cargo build

# Default command
CMD ["cargo", "test"]
"""

    else:
        # Generic Dockerfile
        content = """FROM ubuntu:22.04

WORKDIR /app

COPY . .

CMD ["/bin/bash"]
"""

    with open(dockerfile, "w") as f:
        f.write(content)

    logger.info(f"Created Dockerfile at {dockerfile}")
    return dockerfile


def build_docker_image(
    repo_path: Path,
    commit: str,
    docker_images_path: Path,
    pr_info: PRInfo,
    language: str,
    logger: logging.Logger,
    timeout: int = 900
) -> Optional[str]:
    """
    Build a Docker image from the current repository state.

    The image is built at the BASE commit to capture the environment
    before the PR changes.

    Args:
        repo_path: Path to repository
        commit: Current commit SHA (used in tag)
        docker_images_path: Directory to save image tar
        pr_info: PR information (used in tag)
        language: Repository language
        logger: Logger instance
        timeout: Build timeout in seconds

    Returns:
        Docker image URI (file:// path to tar), or None if failed
    """
    logger.info(f"Building Docker image for commit: {commit[:12]}")

    # Check Docker availability
    if not check_docker_available(logger):
        logger.error("Cannot build Docker image: Docker not available")
        return None

    # Ensure Dockerfile exists
    create_minimal_dockerfile(repo_path, language, logger)

    # Generate image tag
    # Format: owner/repo:base-<commit_short>
    image_tag = f"{pr_info.owner.lower()}/{pr_info.repo.lower()}:base-{commit[:12]}"
    tar_filename = f"base-{commit[:12]}.tar"
    tar_path = docker_images_path / tar_filename

    # Build the image
    logger.info(f"Building image: {image_tag}")
    exit_code, stdout, stderr = run_command(
        ["docker", "build", "-t", image_tag, "."],
        cwd=repo_path,
        logger=logger,
        timeout=timeout
    )

    if exit_code != 0:
        logger.error(f"Docker build failed: {stderr[:500]}")
        return None

    logger.info("Docker build successful")

    # Save image to tar file
    docker_images_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"Saving image to: {tar_path}")
    exit_code, _, stderr = run_command(
        ["docker", "save", "-o", str(tar_path), image_tag],
        cwd=repo_path,
        logger=logger,
        timeout=600
    )

    if exit_code != 0:
        logger.error(f"Docker save failed: {stderr}")
        return None

    # Generate URI
    image_uri = f"file://{tar_path.absolute()}"
    logger.info(f"Docker image saved: {image_uri}")

    return image_uri


def load_docker_image(tar_path: Path, logger: logging.Logger) -> bool:
    """
    Load a Docker image from a tar file.

    Args:
        tar_path: Path to the tar file
        logger: Logger instance

    Returns:
        True if successful
    """
    if not tar_path.exists():
        logger.error(f"Tar file not found: {tar_path}")
        return False

    logger.info(f"Loading Docker image from: {tar_path}")

    exit_code, stdout, stderr = run_command(
        ["docker", "load", "-i", str(tar_path)],
        logger=logger,
        timeout=300
    )

    if exit_code != 0:
        logger.error(f"Docker load failed: {stderr}")
        return False

    logger.info("Docker image loaded successfully")
    return True


def cleanup_docker_image(image_tag: str, logger: logging.Logger) -> None:
    """
    Remove a Docker image.

    Args:
        image_tag: Image tag to remove
        logger: Logger instance
    """
    logger.info(f"Removing Docker image: {image_tag}")

    run_command(
        ["docker", "rmi", "-f", image_tag],
        logger=logger,
        timeout=60
    )
