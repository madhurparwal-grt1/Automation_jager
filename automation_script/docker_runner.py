"""
Docker container orchestration for test execution.

This module handles:
- Running containers from the BASE image
- Applying patches inside containers
- Executing tests in isolated environments
- Collecting results from containers

Directory Structure (inside container):
- /app/repo           - Repository source code (from image)
- /saved/ENV          - Language toolchain and cached dependencies
- /saved/venv/ENV     - Python virtual environment (Python only)
- /workspace          - Evaluation workspace (mounted from host)
"""

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

from .config import (
    DOCKER_MEMORY_LIMIT,
    DOCKER_CPU_LIMIT,
    DOCKER_TIMEOUT_RUN,
    TestResult,
    CONTAINER_REPO_PATH,
    CONTAINER_WORKSPACE_PATH,
)
from .utils import run_command


def run_base_tests(
    image_tag: str,
    workspace_path: Path,
    repo_mount_path: Path,
    test_command: str,
    logger: logging.Logger,
    timeout: int = DOCKER_TIMEOUT_RUN
) -> Optional[TestResult]:
    """
    Run tests on BASE commit inside Docker container.

    The container is ephemeral and removed after execution.
    
    The image contains the repository at /app/repo with all dependencies pre-installed.
    Tests run directly against the image's /app/repo directory.

    Args:
        image_tag: Docker image tag (e.g., pr-eval:base-abc123)
        workspace_path: Host workspace directory (mounted to /workspace)
        repo_mount_path: Host repo directory (not used for BASE tests, kept for API compat)
        test_command: Test command to execute
        logger: Logger instance
        timeout: Container execution timeout

    Returns:
        TestResult or None if failed
    """
    logger.info(f"Running BASE tests in container from image: {image_tag}")

    # Mount automation_script so container_runner module is available inside the container.
    # The repo is already inside the image at /app/repo (cloned during build).
    automation_script_path = Path(__file__).parent.absolute()
    container_cmd = [
        "docker", "run",
        "--rm",  # Remove after exit
        f"--memory={DOCKER_MEMORY_LIMIT}",
        f"--cpus={DOCKER_CPU_LIMIT}",
        "-v", f"{workspace_path.absolute()}:{CONTAINER_WORKSPACE_PATH}",
        "-v", f"{automation_script_path}:/automation_script:ro",
        "-e", "PYTHONPATH=/",
        "-w", CONTAINER_REPO_PATH,
        image_tag,
        "python3", "-m", "automation_script.container_runner",
        "--mode", "base",
        "--repo-path", CONTAINER_REPO_PATH,
        "--test-command", test_command,
        "--output", f"{CONTAINER_WORKSPACE_PATH}/artifacts/base"
    ]

    logger.debug(f"Container command: {' '.join(container_cmd)}")

    # Run container
    exit_code, stdout, stderr = run_command(
        container_cmd,
        logger=logger,
        timeout=timeout
    )

    if exit_code != 0:
        logger.error(f"BASE container failed with exit code {exit_code}")
        logger.debug(f"stdout: {stdout[:500]}")
        logger.debug(f"stderr: {stderr[:500]}")

    # Load result from artifacts
    result_file = workspace_path / "artifacts" / "base" / "result.json"
    if not result_file.exists():
        logger.error("BASE test result file not found")
        return None

    try:
        with open(result_file) as f:
            data = json.load(f)

        result = TestResult(
            success=data["success"],
            exit_code=data["exit_code"],
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            duration=data["duration"],
            tests_passed=data.get("tests_passed", []),
            tests_failed=data.get("tests_failed", []),
            tests_skipped=data.get("tests_skipped", []),
            error_type=data.get("error_type")
        )

        logger.info(f"BASE tests: {len(result.tests_passed)} passed, "
                   f"{len(result.tests_failed)} failed, "
                   f"{len(result.tests_skipped)} skipped")

        return result

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse BASE result: {e}")
        return None


def run_patched_tests(
    image_tag: str,
    workspace_path: Path,
    repo_mount_path: Path,
    patch_path: Path,
    test_command: str,
    logger: logging.Logger,
    timeout: int = DOCKER_TIMEOUT_RUN
) -> Optional[TestResult]:
    """
    Run tests on PATCHED PR commit inside Docker container.

    The patch is applied inside the container to the BASE state at /app/repo.
    The image contains the repository with all dependencies pre-installed.

    Args:
        image_tag: Docker image tag (e.g., pr-eval:base-abc123)
        workspace_path: Host workspace directory (mounted to /workspace)
        repo_mount_path: Host repo directory (not used, kept for API compat)
        patch_path: Path to patch file on host
        test_command: Test command to execute
        logger: Logger instance
        timeout: Container execution timeout

    Returns:
        TestResult or None if failed
    """
    logger.info(f"Running PATCHED tests in container from image: {image_tag}")
    logger.info(f"Patch file: {patch_path}")

    # Verify patch exists
    if not patch_path.exists():
        logger.error(f"Patch file not found: {patch_path}")
        return None

    # Mount automation_script so container_runner module is available.
    # The repo is already inside the image at /app/repo.
    # Patch will be applied inside the container to /app/repo.
    automation_script_path = Path(__file__).parent.absolute()
    container_cmd = [
        "docker", "run",
        "--rm",
        f"--memory={DOCKER_MEMORY_LIMIT}",
        f"--cpus={DOCKER_CPU_LIMIT}",
        "-v", f"{workspace_path.absolute()}:{CONTAINER_WORKSPACE_PATH}",
        "-v", f"{automation_script_path}:/automation_script:ro",
        "-e", "PYTHONPATH=/",
        "-w", CONTAINER_REPO_PATH,
        image_tag,
        "python3", "-m", "automation_script.container_runner",
        "--mode", "patched",
        "--repo-path", CONTAINER_REPO_PATH,
        "--test-command", test_command,
        "--patch", f"{CONTAINER_WORKSPACE_PATH}/patches/pr.patch",
        "--output", f"{CONTAINER_WORKSPACE_PATH}/artifacts/pr"
    ]

    logger.debug(f"Container command: {' '.join(container_cmd)}")

    # Run container
    exit_code, stdout, stderr = run_command(
        container_cmd,
        logger=logger,
        timeout=timeout
    )

    if exit_code != 0:
        logger.warning(f"PATCHED container exited with code {exit_code}")
        logger.debug(f"stdout: {stdout[:500]}")
        logger.debug(f"stderr: {stderr[:500]}")

    # Load result from artifacts
    result_file = workspace_path / "artifacts" / "pr" / "result.json"
    if not result_file.exists():
        logger.error("PATCHED test result file not found")
        return None

    try:
        with open(result_file) as f:
            data = json.load(f)

        result = TestResult(
            success=data["success"],
            exit_code=data["exit_code"],
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            duration=data["duration"],
            tests_passed=data.get("tests_passed", []),
            tests_failed=data.get("tests_failed", []),
            tests_skipped=data.get("tests_skipped", []),
            error_type=data.get("error_type")
        )

        logger.info(f"PATCHED tests: {len(result.tests_passed)} passed, "
                   f"{len(result.tests_failed)} failed, "
                   f"{len(result.tests_skipped)} skipped")

        return result

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse PATCHED result: {e}")
        return None


def run_test_patch_only_tests(
    image_tag: str,
    workspace_path: Path,
    repo_mount_path: Path,
    test_patch_path: Path,
    test_command: str,
    logger: logging.Logger,
    timeout: int = DOCKER_TIMEOUT_RUN
) -> Optional[TestResult]:
    """
    Run tests with ONLY the test_patch applied (Run 2 of three-run sequence).

    This is the critical validation step: new tests added by the PR should FAIL
    when only the test_patch is applied (without the solution/code patch).
    This proves that the new tests actually test the bug/feature being fixed.

    Args:
        image_tag: Docker image tag (e.g., pr-eval:base-abc123)
        workspace_path: Host workspace directory (mounted to /workspace)
        repo_mount_path: Host repo directory (not used, kept for API compat)
        test_patch_path: Path to test-only patch file on host
        test_command: Test command to execute
        logger: Logger instance
        timeout: Container execution timeout

    Returns:
        TestResult or None if failed
    """
    logger.info(f"Running TEST_PATCH_ONLY tests in container (Run 2)")
    logger.info(f"Test patch file: {test_patch_path}")

    # Verify test patch exists
    if not test_patch_path.exists():
        logger.error(f"Test patch file not found: {test_patch_path}")
        return None

    # Check if test patch is empty
    if test_patch_path.stat().st_size == 0:
        logger.warning("Test patch is empty - no test files changed in PR")
        # Return empty result - no new tests to validate
        return TestResult(
            success=True,
            exit_code=0,
            stdout="No test patch to apply - test_patch is empty",
            stderr="",
            duration=0.0,
            tests_passed=[],
            tests_failed=[],
            tests_skipped=[],
            error_type=None
        )

    # Mount automation_script so container_runner module is available.
    # The repo is already inside the image at /app/repo.
    # Test patch will be applied inside the container to /app/repo.
    automation_script_path = Path(__file__).parent.absolute()
    container_cmd = [
        "docker", "run",
        "--rm",
        f"--memory={DOCKER_MEMORY_LIMIT}",
        f"--cpus={DOCKER_CPU_LIMIT}",
        "-v", f"{workspace_path.absolute()}:{CONTAINER_WORKSPACE_PATH}",
        "-v", f"{automation_script_path}:/automation_script:ro",
        "-e", "PYTHONPATH=/",
        "-w", CONTAINER_REPO_PATH,
        image_tag,
        "python3", "-m", "automation_script.container_runner",
        "--mode", "patched",
        "--repo-path", CONTAINER_REPO_PATH,
        "--test-command", test_command,
        "--patch", f"{CONTAINER_WORKSPACE_PATH}/patches/test.patch",
        "--output", f"{CONTAINER_WORKSPACE_PATH}/artifacts/test_patch_only"
    ]

    logger.debug(f"Container command: {' '.join(container_cmd)}")

    # Run container
    exit_code, stdout, stderr = run_command(
        container_cmd,
        logger=logger,
        timeout=timeout
    )

    # Note: We expect tests to fail in Run 2 (that's the validation)
    # So exit_code != 0 is actually expected for valid FAIL_TO_PASS tests
    if exit_code != 0:
        logger.info(f"TEST_PATCH_ONLY container exited with code {exit_code} (expected for F2P tests)")
        logger.debug(f"stdout: {stdout[:500]}")
        logger.debug(f"stderr: {stderr[:500]}")

    # Load result from artifacts
    result_file = workspace_path / "artifacts" / "test_patch_only" / "result.json"
    if not result_file.exists():
        logger.error("TEST_PATCH_ONLY test result file not found")
        return None

    try:
        with open(result_file) as f:
            data = json.load(f)

        result = TestResult(
            success=data["success"],
            exit_code=data["exit_code"],
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            duration=data["duration"],
            tests_passed=data.get("tests_passed", []),
            tests_failed=data.get("tests_failed", []),
            tests_skipped=data.get("tests_skipped", []),
            error_type=data.get("error_type")
        )

        logger.info(f"TEST_PATCH_ONLY tests: {len(result.tests_passed)} passed, "
                   f"{len(result.tests_failed)} failed, "
                   f"{len(result.tests_skipped)} skipped")

        return result

    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse TEST_PATCH_ONLY result: {e}")
        return None


def verify_patch_applies(
    image_tag: str,
    workspace_path: Path,
    repo_mount_path: Path,
    patch_path: Path,
    logger: logging.Logger,
    patch_name: str = "pr.patch"
) -> Tuple[bool, str]:
    """
    Verify that a patch can be applied cleanly inside the container.

    This is a dry-run check before actual test execution.
    The repo is at /app/repo inside the image.

    Args:
        image_tag: Docker image tag
        workspace_path: Host workspace directory
        repo_mount_path: Host repo directory (not used, kept for API compat)
        patch_path: Path to patch file
        logger: Logger instance
        patch_name: Name of patch file in patches/ directory (e.g., "pr.patch", "test.patch")

    Returns:
        Tuple of (success, error_message)
    """
    logger.info(f"Verifying patch can be applied cleanly: {patch_name}")

    if not patch_path.exists():
        return False, f"Patch file not found: {patch_path}"

    # Check if patch is empty
    if patch_path.stat().st_size == 0:
        logger.info(f"Patch {patch_name} is empty - nothing to apply")
        return True, ""

    # Test patch application in container against /app/repo
    container_cmd = [
        "docker", "run",
        "--rm",
        "-v", f"{workspace_path.absolute()}:{CONTAINER_WORKSPACE_PATH}",
        "-w", CONTAINER_REPO_PATH,
        image_tag,
        "git", "apply", "--check", f"{CONTAINER_WORKSPACE_PATH}/patches/{patch_name}"
    ]

    exit_code, stdout, stderr = run_command(
        container_cmd,
        logger=logger,
        timeout=60
    )

    if exit_code != 0:
        error_msg = f"Patch does not apply cleanly: {stderr}"
        logger.error(error_msg)
        return False, error_msg

    logger.info("Patch verification successful")
    return True, ""


def save_docker_image(
    image_tag: str,
    output_path: Path,
    logger: logging.Logger
) -> bool:
    """
    Save a Docker image to uncompressed tar format.

    This creates the canonical benchmark artifact.

    Args:
        image_tag: Docker image tag to save
        output_path: Output path for tar file
        logger: Logger instance

    Returns:
        True if successful
    """
    logger.info(f"Saving Docker image: {image_tag}")
    logger.info(f"Output: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save image to tar (uncompressed)
    logger.info("Saving Docker image to tar...")
    exit_code, _, stderr = run_command(
        ["docker", "save", "-o", str(output_path), image_tag],
        logger=logger,
        timeout=600
    )

    if exit_code != 0:
        logger.error(f"Failed to save Docker image: {stderr}")
        return False

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"Docker image saved: {size_mb:.1f} MB")
    return True


# Alias for backward compatibility
compress_docker_image = save_docker_image


def load_docker_image(
    tar_path: Path,
    logger: logging.Logger
) -> bool:
    """
    Load a Docker image from tar file.

    Args:
        tar_path: Path to tar file
        logger: Logger instance

    Returns:
        True if successful
    """
    if not tar_path.exists():
        logger.error(f"Docker image not found: {tar_path}")
        return False

    logger.info(f"Loading Docker image: {tar_path}")

    try:
        exit_code, stdout, stderr = run_command(
            ["docker", "load", "-i", str(tar_path)],
            logger=logger,
            timeout=600
        )

        if exit_code != 0:
            logger.error(f"Failed to load image: {stderr}")
            return False

        logger.info("Docker image loaded successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to load Docker image: {e}")
        return False


# Alias for backward compatibility
load_compressed_image = load_docker_image
