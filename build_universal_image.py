#!/usr/bin/env python3
"""
Build Universal Multi-Architecture Docker Image

This script uses docker buildx to create a single multi-architecture Docker image
(supporting linux/amd64 and linux/arm64) from an existing Dockerfile and saves
it as a single universal OCI-compliant .tar file.

Usage:
    python build_universal_image.py --dockerfile <path> --output <path> --repo_url <url> --commit <sha>

Example:
    python build_universal_image.py \
        --dockerfile ./Dockerfile \
        --output ./image.tar \
        --repo_url https://github.com/example/repo.git \
        --commit abc123def456
"""

import argparse
import os
import sys
import logging
from pathlib import Path

from automation_script.utils import run_command, run_command_with_output

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Constants
BUILDER_NAME = "multi-arch-builder"
PLATFORMS = "linux/amd64,linux/arm64"
CACHE_DIR = "./.buildx-cache"


def check_docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        run_command("docker info", capture_output=True, check=True)
        return True
    except Exception as e:
        logger.error(f"Docker is not available: {e}")
        return False


def check_buildx_available() -> bool:
    """Check if docker buildx is available."""
    try:
        run_command("docker buildx version", capture_output=True, check=True)
        return True
    except Exception as e:
        logger.error(f"Docker buildx is not available: {e}")
        return False


def builder_exists(builder_name: str) -> bool:
    """Check if a buildx builder with the given name exists."""
    try:
        output = run_command_with_output(
            "docker buildx ls",
            check=True
        )
        # Check if builder name appears in the list
        for line in output.split('\n'):
            if line.startswith(builder_name) or line.startswith(f"{builder_name} "):
                return True
        return False
    except Exception:
        return False


def create_builder(builder_name: str) -> None:
    """Create a new buildx builder with docker-container driver."""
    logger.info(f"Creating buildx builder '{builder_name}' with docker-container driver...")
    
    run_command(
        f"docker buildx create --name {builder_name} --driver docker-container --bootstrap",
        check=True
    )
    logger.info(f"Builder '{builder_name}' created and bootstrapped successfully.")


def use_builder(builder_name: str) -> None:
    """Set the specified builder as the current builder."""
    logger.info(f"Setting '{builder_name}' as the current builder...")
    run_command(f"docker buildx use {builder_name}", check=True)


def ensure_builder_ready(builder_name: str) -> None:
    """
    Ensure the buildx builder is ready for use.
    Creates it if it doesn't exist, then sets it as current.
    """
    if builder_exists(builder_name):
        logger.info(f"Builder '{builder_name}' already exists.")
    else:
        logger.info(f"Builder '{builder_name}' not found. Creating...")
        create_builder(builder_name)
    
    use_builder(builder_name)
    
    # Verify builder is ready by inspecting it
    logger.info(f"Inspecting builder '{builder_name}'...")
    run_command(f"docker buildx inspect {builder_name} --bootstrap", check=True)


def ensure_cache_dir_exists() -> None:
    """Ensure the cache directory exists."""
    cache_path = Path(CACHE_DIR)
    if not cache_path.exists():
        logger.info(f"Creating cache directory: {CACHE_DIR}")
        cache_path.mkdir(parents=True, exist_ok=True)


def build_multi_arch_image(
    dockerfile: str,
    output_path: str,
    repo_url: str,
    commit: str,
    context: str = "."
) -> None:
    """
    Build a multi-architecture Docker image and save as OCI tarball.

    Args:
        dockerfile: Path to the Dockerfile
        output_path: Path for the output .tar file
        repo_url: Repository URL to pass as build argument
        commit: Commit SHA to pass as build argument
        context: Build context directory (default: current directory)
    """
    # Ensure output directory exists
    output_dir = Path(output_path).parent
    if output_dir and not output_dir.exists():
        logger.info(f"Creating output directory: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)

    # Ensure cache directory exists
    ensure_cache_dir_exists()

    # Build the buildx command
    build_cmd = [
        "docker", "buildx", "build",
        # Target platforms
        f"--platform={PLATFORMS}",
        # Dockerfile location
        f"--file={dockerfile}",
        # Build arguments
        f"--build-arg=REPO_URL={repo_url}",
        f"--build-arg=BASE_COMMIT={commit}",
        # Cache configuration
        f"--cache-from=type=local,src={CACHE_DIR}",
        f"--cache-to=type=local,dest={CACHE_DIR},mode=max",
        # OCI output format - creates a single tarball with all architectures
        f"--output=type=oci,dest={output_path}",
        # Build context
        context
    ]

    cmd_str = " ".join(build_cmd)
    
    logger.info("Starting multi-architecture build...")
    logger.info(f"  Platforms: {PLATFORMS}")
    logger.info(f"  Dockerfile: {dockerfile}")
    logger.info(f"  Output: {output_path}")
    logger.info(f"  REPO_URL: {repo_url}")
    logger.info(f"  BASE_COMMIT: {commit}")

    run_command(cmd_str, check=True)

    # Verify output file was created
    if Path(output_path).exists():
        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        logger.info(f"Successfully created multi-arch image: {output_path} ({size_mb:.2f} MB)")
    else:
        raise RuntimeError(f"Build completed but output file not found: {output_path}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Build a universal multi-architecture Docker image using buildx",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Build from a Dockerfile in the current directory
    python build_universal_image.py \\
        --dockerfile ./Dockerfile \\
        --output ./my-image.tar \\
        --repo_url https://github.com/example/repo.git \\
        --commit abc123

    # Specify a custom build context
    python build_universal_image.py \\
        --dockerfile ./docker/Dockerfile.prod \\
        --output ./dist/image.tar \\
        --repo_url https://github.com/example/repo.git \\
        --commit $(git rev-parse HEAD) \\
        --context ./src

The output .tar file is OCI-compliant and contains both linux/amd64 and 
linux/arm64 variants. When loaded with 'docker load', Docker will 
automatically select the correct architecture for the host system.
        """
    )

    parser.add_argument(
        "--dockerfile",
        required=True,
        help="Path to the Dockerfile to build"
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Path for the output .tar file (OCI format)"
    )

    parser.add_argument(
        "--repo_url",
        required=True,
        help="Repository URL to pass as REPO_URL build argument"
    )

    parser.add_argument(
        "--commit",
        required=True,
        help="Commit SHA to pass as BASE_COMMIT build argument"
    )

    parser.add_argument(
        "--context",
        default=".",
        help="Build context directory (default: current directory)"
    )

    parser.add_argument(
        "--builder-name",
        default=BUILDER_NAME,
        help=f"Name of the buildx builder to use (default: {BUILDER_NAME})"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Configure log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("Universal Multi-Architecture Docker Image Builder")
    logger.info("=" * 60)

    try:
        # Pre-flight checks
        logger.info("Running pre-flight checks...")
        
        if not check_docker_available():
            logger.error("Docker is not running. Please start Docker and try again.")
            return 1

        if not check_buildx_available():
            logger.error("Docker buildx is not available. Please install/enable buildx.")
            return 1

        # Verify Dockerfile exists
        if not Path(args.dockerfile).exists():
            logger.error(f"Dockerfile not found: {args.dockerfile}")
            return 1

        # Verify context exists
        if not Path(args.context).exists():
            logger.error(f"Build context not found: {args.context}")
            return 1

        # Initialize buildx builder
        ensure_builder_ready(args.builder_name)

        # Build the multi-arch image
        build_multi_arch_image(
            dockerfile=args.dockerfile,
            output_path=args.output,
            repo_url=args.repo_url,
            commit=args.commit,
            context=args.context
        )

        logger.info("=" * 60)
        logger.info("Build completed successfully!")
        logger.info(f"Output: {args.output}")
        logger.info("")
        logger.info("To load this image on any architecture:")
        logger.info(f"  docker load < {args.output}")
        logger.info("=" * 60)

        return 0

    except KeyboardInterrupt:
        logger.warning("Build interrupted by user.")
        return 130

    except Exception as e:
        logger.error(f"Build failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
