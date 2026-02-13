#!/usr/bin/env python3
"""
PART 1: Docker Image Creation and BASE Testing

This script handles:
- Repository cloning
- PR reference fetching
- BASE commit determination
- Docker image building
- BASE test execution
- Image compression and saving
- State file generation for Part 2

Output: Immutable Docker image + BASE test results + state.json
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import shutil

from .config import (
    ARTIFACTS_DIR, DOCKER_IMAGES_DIR, PATCHES_DIR, METADATA_DIR, LOGS_DIR,
    DOCKER_MEMORY_LIMIT, DOCKER_CPU_LIMIT, DOCKER_TIMEOUT_BUILD, DOCKER_TIMEOUT_RUN,
    TestResult
)
from .git_wrappers import (
    clone_repository, fetch_pr_refs, get_pr_head_commit
)
from .git_operations import (
    detect_target_branch, get_base_commit, checkout_commit, parse_pr_url
)
from .docker_builder_new import build_docker_image, save_and_compress_image
from .docker_runner import run_base_tests
from .language_detection import detect_language_and_test_command
from .environment import detect_language, detect_test_command
from .docker_healing import (
    detect_docker_build_error_type,
    detect_test_error_type,
    should_retry_docker_build,
    should_retry_test_execution,
    apply_docker_build_healing,
    apply_test_execution_healing,
    analyze_test_stability
)
from .utils import run_command
from .cleanup import safe_rmtree
import subprocess


# =============================================================================
# Retry Configuration
# =============================================================================

MAX_DOCKER_BUILD_RETRIES = 3
MAX_TEST_EXECUTION_RETRIES = 3


# =============================================================================
# Helper Functions with Retry Logic
# =============================================================================

def build_docker_image_with_retry(
    repo_path: Path,
    base_commit: str,
    language: str,
    logger: logging.Logger,
    pr_number: Optional[int] = None,
    repo_full_name: Optional[str] = None
) -> Optional[str]:
    """
    Build Docker image with retry logic and healing.

    Retries up to MAX_DOCKER_BUILD_RETRIES times, applying healing
    strategies between attempts.

    Args:
        repo_path: Path to repository
        base_commit: Base commit SHA
        language: Repository language
        logger: Logger instance
        pr_number: Optional PR number (for unique image tagging)
        repo_full_name: Optional full repository name (e.g., "owner/repo") for config lookup

    Returns:
        Image tag if successful, None otherwise
    """
    logger.info("=" * 80)
    logger.info("BUILDING DOCKER IMAGE WITH RETRY/HEALING LOGIC")
    logger.info("=" * 80)
    logger.info(f"Max retries: {MAX_DOCKER_BUILD_RETRIES}")
    if pr_number:
        logger.info(f"PR number: {pr_number} (will be included in image tag)")
    logger.info("")

    image_tag = None
    last_stderr = ""

    for attempt in range(MAX_DOCKER_BUILD_RETRIES):
        logger.info(f"Docker build attempt {attempt + 1}/{MAX_DOCKER_BUILD_RETRIES}")
        logger.info("-" * 40)

        # Build image
        image_tag = build_docker_image(repo_path, base_commit, language, logger, pr_number=pr_number, repo_full_name=repo_full_name)

        if image_tag:
            logger.info(f"✓ Docker build successful on attempt {attempt + 1}")
            return image_tag

        # Build failed - detect error type
        logger.warning(f"✗ Docker build failed on attempt {attempt + 1}")

        # Get the build error from docker build output
        # We need to capture stderr from the build command
        # For now, we'll check if we should retry based on attempt number
        error_type = detect_docker_build_error_type(last_stderr)
        logger.info(f"Detected error type: {error_type}")

        # Check if we should retry
        if not should_retry_docker_build(attempt, MAX_DOCKER_BUILD_RETRIES - 1, error_type, logger):
            logger.error(f"Docker build failed and error is not retriable")
            return None

        # Apply healing strategy
        logger.info("")
        logger.info("Applying healing strategy...")
        logger.info("-" * 40)

        healed = apply_docker_build_healing(
            repo_path=repo_path,
            language=language,
            attempt=attempt,
            error_type=error_type,
            logger=logger
        )

        if healed:
            logger.info("✓ Healing strategy applied, retrying...")
        else:
            logger.info("⚠ No specific healing applied, will retry anyway...")

        logger.info("")

    logger.error(f"Docker build failed after {MAX_DOCKER_BUILD_RETRIES} attempts")
    return None


def run_base_tests_with_retry(
    image_tag: str,
    workspace_path: Path,
    repo_mount_path: Path,
    test_command: str,
    language: str,
    logger: logging.Logger,
    base_commit: str = None,
    pr_number: int = None,
    repo_full_name: str = None
) -> Optional[TestResult]:
    """
    Run BASE tests with retry logic and healing.

    Retries up to MAX_TEST_EXECUTION_RETRIES times, applying healing
    strategies and checking for test stability.

    Args:
        image_tag: Docker image tag
        workspace_path: Workspace directory
        repo_mount_path: Repository mount path
        test_command: Test command to execute
        language: Repository language
        logger: Logger instance
        base_commit: Base commit SHA (needed for Docker image rebuild)
        pr_number: PR number (needed for Docker image rebuild)
        repo_full_name: Full repository name (needed for Docker image rebuild)

    Returns:
        TestResult or None if all attempts failed
    """
    logger.info("=" * 80)
    logger.info("RUNNING BASE TESTS WITH RETRY/HEALING LOGIC")
    logger.info("=" * 80)
    logger.info(f"Max retries: {MAX_TEST_EXECUTION_RETRIES}")
    logger.info("")

    test_results: List[TestResult] = []
    timeout = DOCKER_TIMEOUT_RUN
    current_image_tag = image_tag

    for attempt in range(MAX_TEST_EXECUTION_RETRIES):
        logger.info(f"Test execution attempt {attempt + 1}/{MAX_TEST_EXECUTION_RETRIES}")
        logger.info("-" * 40)

        # Run tests
        test_result = run_base_tests(
            image_tag=current_image_tag,
            workspace_path=workspace_path,
            repo_mount_path=repo_mount_path,
            test_command=test_command,
            logger=logger,
            timeout=int(timeout)
        )

        if test_result is None:
            logger.error(f"✗ Test execution failed to produce results on attempt {attempt + 1}")
            continue

        test_results.append(test_result)

        # Log results
        logger.info(f"Test results: {len(test_result.tests_passed)} passed, "
                   f"{len(test_result.tests_failed)} failed, "
                   f"{len(test_result.tests_skipped)} skipped")

        # Detect error type
        error_type = detect_test_error_type(test_result)
        logger.info(f"Detected error type: {error_type or 'None (just test failures)'}")

        # Check test stability
        stability = analyze_test_stability(test_results, logger)
        logger.info(f"Test stability: {stability['reason']}")

        # If tests are stable and no environment errors, we're done
        if stability['stable']:
            logger.info(f"✓ Tests are stable after {attempt + 1} attempt(s)")
            return test_result

        # Check if we should retry
        if not should_retry_test_execution(
            attempt, MAX_TEST_EXECUTION_RETRIES - 1, test_result, error_type, logger
        ):
            logger.info(f"No retry needed - returning results from attempt {attempt + 1}")
            return test_result

        # Apply healing strategy
        logger.info("")
        logger.info("Applying healing strategy...")
        logger.info("-" * 40)

        modifications = apply_test_execution_healing(
            repo_path=repo_mount_path,
            language=language,
            attempt=attempt,
            error_type=error_type,
            test_result=test_result,
            logger=logger
        )

        # Check if the error is not healable (e.g., requires Nix package manager)
        if modifications.get('not_healable'):
            error_reason = modifications.get('error_reason', 'unknown')
            logger.error("")
            logger.error("=" * 60)
            logger.error(f"ERROR IS NOT HEALABLE: {error_reason}")
            logger.error("=" * 60)
            logger.error("This error cannot be fixed with Docker-based testing.")
            logger.error("Stopping retry attempts.")
            logger.info("")
            return test_result

        # Check if Docker image needs to be rebuilt (e.g., for Rust nightly switch or missing system libs)
        if modifications.get('rebuild_docker_image') and base_commit:
            logger.info("")
            logger.info("=" * 60)
            logger.info("REBUILDING DOCKER IMAGE (self-healing)")
            logger.info("=" * 60)
            logger.info("Reason: Environment requires different toolchain version or missing libraries")

            # Rebuild the Docker image with the updated Dockerfile
            # Use no_cache=True to force rebuild with new packages
            new_image_tag = build_docker_image(
                repo_path=repo_mount_path,
                base_commit=base_commit,
                language=language,
                logger=logger,
                pr_number=pr_number,
                repo_full_name=repo_full_name,
                no_cache=True  # Force fresh build after Dockerfile changes
            )

            if new_image_tag:
                current_image_tag = new_image_tag
                logger.info(f"✓ Docker image rebuilt successfully: {current_image_tag}")
            else:
                logger.error("✗ Failed to rebuild Docker image")
                # Continue with old image, though it will likely fail again

        # Apply other modifications
        if 'timeout_multiplier' in modifications:
            timeout *= modifications['timeout_multiplier']
            logger.info(f"Increased timeout to {timeout:.0f}s")

        logger.info("Retrying test execution...")
        logger.info("")

    # Return the last result
    if test_results:
        logger.warning(f"Tests did not stabilize after {MAX_TEST_EXECUTION_RETRIES} attempts")
        logger.warning("Returning results from last attempt")
        return test_results[-1]

    logger.error(f"All {MAX_TEST_EXECUTION_RETRIES} test execution attempts failed")
    return None


def setup_logging(workspace_root: Path, external_logger: logging.Logger = None) -> logging.Logger:
    """
    Set up logging for Part 1.
    
    Args:
        workspace_root: Path to the workspace directory
        external_logger: Optional external logger (from orchestrator) for unified logging
        
    Returns:
        Logger instance to use for Part 1 operations
    """
    # If external logger provided (unified logging mode), use it
    if external_logger is not None:
        # Create a child logger that inherits from the external logger
        logger = logging.getLogger("part1")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.parent = external_logger
        logger.propagate = True
        return logger
    
    # Standalone mode: create own file handler (for backward compatibility)
    logs_dir = workspace_root / LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_file = logs_dir / "part1_build_and_base.log"

    logger = logging.getLogger("part1")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # File handler
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    ))

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    ))

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


def save_state(workspace_root: Path, state: Dict[str, Any], logger: logging.Logger):
    """Save state.json for Part 2 to consume."""
    state_file = workspace_root / "state.json"

    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)

    logger.info(f"State saved to {state_file}")
    logger.info(f"State summary:")
    logger.info(f"  - repo: {state.get('repo')}")
    logger.info(f"  - pr_number: {state.get('pr_number')}")
    logger.info(f"  - base_commit: {state.get('base_commit')}")
    logger.info(f"  - pr_commit: {state.get('pr_commit')}")
    logger.info(f"  - language: {state.get('language')}")
    logger.info(f"  - docker_image: {state.get('docker_image')}")
    logger.info(f"  - image_uri: {state.get('image_uri')}")


def run_part1(pr_url: str, output_dataset_root: str, overrides: dict = None, logger: logging.Logger = None) -> tuple:
    """
    Execute Part 1: Build Docker image and run BASE tests.

    Args:
        pr_url: Full PR URL
        output_dataset_root: Root directory for all output datasets (e.g., ./Output_dataset)
        overrides: Optional dict with keys:
            - 'language': Override detected language (e.g., 'rust', 'python')
            - 'test_command': Override detected test command
            - 'rust_subdir': For Rust projects, subdirectory containing Cargo.toml
            - 'shallow_clone': Use shallow clone for faster cloning
            - 'reuse_image': Existing Docker image tag to reuse
            - 'skip_tests': Skip running tests
        logger: Optional external logger for unified logging

    Returns:
        Tuple of (exit_code, workspace_path) where exit_code is 0 on success
    """
    overrides = overrides or {}
    
    # First parse PR URL to get PR info for creating the workspace
    temp_logger = logging.getLogger("temp_parser")
    temp_logger.setLevel(logging.INFO)
    temp_handler = logging.StreamHandler(sys.stdout)
    temp_handler.setFormatter(logging.Formatter('%(message)s'))
    temp_logger.addHandler(temp_handler)
    
    pr_info = parse_pr_url(pr_url, temp_logger)
    if not pr_info:
        print(f"ERROR: Failed to parse PR URL: {pr_url}")
        return (1, None)
    
    # Create PR-specific workspace directory
    # Format: Output_dataset/<owner>-<repo>_pr_<number>
    output_dataset_path = Path(output_dataset_root).resolve()
    output_dataset_path.mkdir(parents=True, exist_ok=True)
    
    pr_folder_name = f"{pr_info.owner}-{pr_info.repo}_pr_{pr_info.pr_number}".replace("/", "-")
    workspace_path = output_dataset_path / pr_folder_name
    workspace_path.mkdir(parents=True, exist_ok=True)

    # Set up logging - use external logger if provided (unified logging)
    logger = setup_logging(workspace_path, external_logger=logger)

    logger.info("=" * 80)
    logger.info("PART 1: DOCKER IMAGE CREATION AND BASE TESTING")
    logger.info("=" * 80)
    logger.info(f"PR URL:             {pr_url}")
    logger.info(f"Output Dataset:     {output_dataset_path}")
    logger.info(f"PR Workspace:       {workspace_path}")
    logger.info(f"PR Folder:          {pr_folder_name}")
    if overrides:
        logger.info(f"Overrides:          {list(overrides.keys())}")
    logger.info("=" * 80)
    logger.info("")

    try:
        # ========== STEP 1: PR Info Already Parsed ==========
        logger.info("[STEP 1/8] PR Information")
        logger.info("-" * 40)
        repo = f"{pr_info.owner}/{pr_info.repo}"
        pr_number = pr_info.pr_number
        logger.info(f"Repository: {repo}")
        logger.info(f"PR Number:  {pr_number}")
        logger.info(f"Platform:   {pr_info.host}")
        logger.info("")

        # ========== STEP 2: Clone Repository ==========
        logger.info("[STEP 2/8] Cloning repository")
        logger.info("-" * 40)
        repo_path = workspace_path / "repo"

        if repo_path.exists():
            logger.info(f"Removing existing repo at {repo_path}")
            safe_rmtree(repo_path, logger)

        # Use shallow clone if requested (faster for large repos)
        use_shallow = overrides.get('shallow_clone', False)
        if use_shallow:
            logger.info("Using shallow clone (--shallow-clone enabled)")
            # Shallow clone with depth 1, then unshallow specific refs as needed
            exit_code, _, stderr = run_command(
                ["git", "clone", "--depth", "1", pr_info.clone_url, str(repo_path)],
                logger=logger,
                timeout=300
            )
            if exit_code != 0:
                logger.error(f"Shallow clone failed: {stderr}")
                return (1, str(workspace_path))
            logger.info("Shallow clone completed - will deepen for merge-base calculation")
        else:
            if not clone_repository(pr_info.clone_url, repo_path, logger):
                logger.error("Failed to clone repository")
                return (1, str(workspace_path))
        logger.info("")

        # ========== STEP 3: Fetch PR refs and determine commits ==========
        logger.info("[STEP 3/8] Fetching PR refs and determining commits")
        logger.info("-" * 40)

        # If shallow clone, need to deepen to fetch PR refs
        if use_shallow:
            logger.info("Deepening shallow clone to fetch PR refs...")
            # Fetch the specific PR ref
            exit_code, _, stderr = run_command(
                ["git", "fetch", "--depth", "100", "origin", f"+refs/pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}"],
                cwd=repo_path,
                logger=logger,
                timeout=120
            )
            if exit_code != 0:
                logger.warning(f"Initial PR fetch failed, deepening further: {stderr}")
                # Deepen the clone to get more history
                run_command(
                    ["git", "fetch", "--deepen", "500", "origin"],
                    cwd=repo_path,
                    logger=logger,
                    timeout=180
                )
        
        if not fetch_pr_refs(repo_path, pr_number, logger):
            logger.error("Failed to fetch PR refs")
            logger.error("TIP: If this fails with shallow clone, try without --shallow-clone")
            return (1, str(workspace_path))

        pr_commit = get_pr_head_commit(repo_path, pr_number, logger)
        if not pr_commit:
            logger.error("Failed to get PR head commit")
            return (1, str(workspace_path))

        # For shallow clones, need to deepen to find merge-base
        if use_shallow:
            logger.info("Deepening shallow clone for merge-base calculation...")
            run_command(
                ["git", "fetch", "--deepen", "1000", "origin"],
                cwd=repo_path,
                logger=logger,
                timeout=180
            )

        target_branch = detect_target_branch(repo_path, logger)
        if not target_branch:
            logger.error("Failed to detect target branch")
            return (1, str(workspace_path))

        # Check for base commit override
        if 'base_commit' in overrides:
            base_commit = overrides['base_commit']
            logger.info(f"BASE commit (OVERRIDE): {base_commit[:12]}")
        else:
            base_commit = get_base_commit(repo_path, target_branch, pr_commit, pr_info, logger)
            if not base_commit:
                logger.error("Failed to determine base commit")
                if use_shallow:
                    logger.error("TIP: Shallow clone may not have enough history. Try without --shallow-clone")
                logger.error("TIP: For merged PRs, use --base-commit <SHA> to specify the base commit manually")
                return (1, str(workspace_path))

        logger.info(f"Target branch: {target_branch}")
        logger.info(f"BASE commit:   {base_commit[:12]}")
        logger.info(f"PR commit:     {pr_commit[:12]}")
        logger.info("")

        # ========== STEP 4: Checkout BASE commit ==========
        logger.info("[STEP 4/8] Checking out BASE commit")
        logger.info("-" * 40)

        if not checkout_commit(repo_path, base_commit, logger):
            logger.error("Failed to checkout BASE commit")
            return (1, str(workspace_path))
        logger.info("")

        # ========== STEP 5: Detect language and test command ==========
        logger.info("[STEP 5/8] Detecting language and test command")
        logger.info("-" * 40)

        # Get list of files changed in the PR (for accurate language detection in polyglot repos)
        logger.info("Getting list of files changed in PR...")
        changed_files = []
        exit_code, stdout, stderr = run_command(
            ["git", "diff", "--name-only", base_commit, pr_commit],
            cwd=repo_path,
            logger=logger
        )
        if exit_code == 0 and stdout:
            changed_files = [f.strip() for f in stdout.strip().split('\n') if f.strip()]
            logger.info(f"PR changes {len(changed_files)} files")
            if changed_files:
                # Log sample of changed files
                sample = changed_files[:5]
                logger.info(f"Sample changed files: {sample}")
        else:
            logger.warning("Could not get changed files, will detect language from entire repo")

        # Construct full repository name for config lookup
        repo_full_name = f"{pr_info.owner}/{pr_info.repo}"
        
        # Check for overrides FIRST (this is the key fix - user overrides always win)
        if 'language' in overrides:
            language = overrides['language']
            logger.info(f"Language (OVERRIDE): {language}")
        else:
            language = detect_language(repo_path, logger, changed_files=changed_files, repo_full_name=repo_full_name)
            logger.info(f"Language (detected): {language}")
        
        if 'test_command' in overrides:
            test_command = overrides['test_command']
            logger.info(f"Test command (OVERRIDE): {test_command}")
        else:
            # Handle rust_subdir override for Rust projects
            if language == 'rust' and 'rust_subdir' in overrides:
                rust_subdir = overrides['rust_subdir']
                test_command = f"cargo test --manifest-path {rust_subdir}/Cargo.toml"
                logger.info(f"Test command (from rust_subdir): {test_command}")
            else:
                test_command = detect_test_command(repo_path, language, logger, repo_full_name=repo_full_name, changed_files=changed_files)
                logger.info(f"Test command (detected): {test_command}")
        
        logger.info("")

        # ========== STEP 6: Build Docker image (with retry) ==========
        logger.info("[STEP 6/8] Building Docker image at BASE commit")
        logger.info("-" * 40)
        
        # Check if we should reuse an existing image
        if 'reuse_image' in overrides:
            image_tag = overrides['reuse_image']
            logger.info(f"⏩ REUSING existing Docker image: {image_tag}")
            logger.info("Skipping Docker build (--reuse-image specified)")
            
            # Verify the image exists
            exit_code, _, stderr = run_command(
                ["docker", "image", "inspect", image_tag],
                logger=logger,
                timeout=30
            )
            if exit_code != 0:
                logger.error(f"Specified image does not exist: {image_tag}")
                logger.error("Please check the image tag or remove --reuse-image to build a new image")
                return (1, str(workspace_path))
            logger.info("✓ Image exists and is valid")
        else:
            logger.info("⚠️  This image is IMMUTABLE and will be used for both BASE and PR tests")
            logger.info(f"⚙️  Retry enabled: Up to {MAX_DOCKER_BUILD_RETRIES} attempts with healing")
            logger.info("")

            image_tag = build_docker_image_with_retry(repo_path, base_commit, language, logger, pr_number=pr_info.pr_number, repo_full_name=repo_full_name)
            
            if not image_tag:
                logger.error("Failed to build Docker image after all retry attempts")
                logger.error("")
                logger.error("TROUBLESHOOTING:")
                logger.error("  1. Check logs/part1_build_and_base.log for details")
                logger.error("  2. Check repo/docker_build_error.log for Docker build output")
                logger.error("  3. Try with different --language if auto-detection was wrong")
                logger.error(f"  4. Current detected language: {language}")
                return (1, str(workspace_path))
        logger.info("")

        # ========== STEP 7: Save and compress Docker image ==========
        logger.info("[STEP 7/8] Saving and compressing Docker image")
        logger.info("-" * 40)

        docker_images_dir = workspace_path / DOCKER_IMAGES_DIR
        docker_images_dir.mkdir(parents=True, exist_ok=True)

        image_uri = save_and_compress_image(
            image_tag,
            docker_images_dir,
            base_commit,
            logger,
            repo_full_name=repo,
            repo_path=repo_path,
            save_dockerfile=True
        )
        if not image_uri:
            logger.error("Failed to save and compress Docker image")
            return (1, str(workspace_path))
        logger.info("")

        # ========== STEP 8: Run BASE tests (with retry) ==========
        logger.info("[STEP 8/8] Running BASE tests in container")
        logger.info("-" * 40)
        
        base_artifacts_dir = workspace_path / ARTIFACTS_DIR / "base"
        base_artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if we should skip tests
        skip_tests = overrides.get('skip_tests', False)
        base_result = None
        
        if skip_tests:
            logger.info("⏩ SKIPPING test execution (--skip-tests specified)")
            logger.info("Creating placeholder result for Part 2")
            
            # Create a placeholder result
            import json
            placeholder_result = {
                "success": True,
                "exit_code": 0,
                "stdout": "Tests skipped via --skip-tests",
                "stderr": "",
                "duration": 0.0,
                "tests_passed": [],
                "tests_failed": [],
                "tests_skipped": [],
                "error_type": None
            }
            result_file = base_artifacts_dir / "result.json"
            with open(result_file, 'w') as f:
                json.dump(placeholder_result, f, indent=2)
            
            # Also create results.jsonl
            jsonl_file = base_artifacts_dir / "results.jsonl"
            with open(jsonl_file, 'w') as f:
                f.write(json.dumps({"status": "skipped", "message": "Tests skipped via --skip-tests"}) + "\n")
            
            logger.info(f"✓ Placeholder result saved to {result_file}")
        else:
            logger.info("Container: ephemeral, spawned from BASE image")
            logger.info(f"⚙️  Retry enabled: Up to {MAX_TEST_EXECUTION_RETRIES} attempts with healing")
            logger.info("")

            base_result = run_base_tests_with_retry(
                image_tag=image_tag,
                workspace_path=workspace_path,
                repo_mount_path=repo_path,
                test_command=test_command,
                language=language,
                logger=logger,
                base_commit=base_commit,
                pr_number=pr_number,
                repo_full_name=repo_full_name
            )

            if base_result is None:
                logger.error("BASE tests did not produce results after all retry attempts")
                # Don't fail - continue to save state
            else:
                logger.info("")
                logger.info("=" * 80)
                logger.info("FINAL BASE TEST RESULTS")
                logger.info("=" * 80)
                logger.info(f"✓ Tests passed:  {len(base_result.tests_passed)}")
                logger.info(f"✗ Tests failed:  {len(base_result.tests_failed)}")
                logger.info(f"⊘ Tests skipped: {len(base_result.tests_skipped)}")
                logger.info(f"Exit code:       {base_result.exit_code}")
                logger.info(f"Duration:        {base_result.duration:.2f}s")

                # Detect if there are environment errors
                error_type = detect_test_error_type(base_result)
                if error_type:
                    logger.warning(f"⚠️  Environment error detected: {error_type}")
                    logger.warning("    This may affect Part 2 evaluation")
                else:
                    logger.info("✓ No environment errors detected")

                logger.info("=" * 80)
        logger.info("")

        # ========== Save state for Part 2 ==========
        logger.info("Generating state file for Part 2")
        logger.info("-" * 40)

        # Convert TestResult to dict for JSON serialization
        base_result_dict = None
        if base_result:
            base_result_dict = {
                "success": base_result.success,
                "exit_code": base_result.exit_code,
                "passed": len(base_result.tests_passed),
                "failed": len(base_result.tests_failed),
                "skipped": len(base_result.tests_skipped),
                "duration": base_result.duration
            }

        state = {
            "pr_url": pr_url,
            "repo": repo,
            "pr_number": pr_number,
            "base_commit": base_commit,
            "pr_commit": pr_commit,
            "target_branch": target_branch,
            "language": language,
            "test_command": test_command,
            "docker_image": image_tag,
            "image_uri": image_uri,
            "repo_path": str(repo_path),
            "workspace_path": str(workspace_path),
            "base_artifacts_dir": str(base_artifacts_dir),
            "base_result": base_result_dict,
            "part1_completed": True
        }

        save_state(workspace_path, state, logger)
        logger.info("")

        # ========== Summary ==========
        logger.info("=" * 80)
        logger.info("PART 1 COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        logger.info(f"PR Workspace:   {workspace_path}")
        logger.info(f"PR Folder:      {pr_folder_name}")
        logger.info(f"Docker image:   {image_tag}")
        logger.info(f"Image URI:      {image_uri}")
        logger.info(f"BASE artifacts: {base_artifacts_dir}")
        logger.info(f"State file:     {workspace_path / 'state.json'}")
        logger.info("")
        logger.info("Output Structure:")
        logger.info(f"  {output_dataset_path.name}/")
        logger.info(f"    └── {pr_folder_name}/")
        logger.info(f"        ├── artifacts/")
        logger.info(f"        ├── docker_images/")
        logger.info(f"        ├── logs/")
        logger.info(f"        ├── metadata/")
        logger.info(f"        └── results/")
        logger.info("")
        logger.info("Next: Run Part 2 to apply patch and evaluate PR")
        logger.info(f"Command: python -m automation_script.part2_patch_and_evaluate {workspace_path}")
        logger.info("=" * 80)

        return (0, str(workspace_path))

    except Exception as e:
        logger.exception(f"Part 1 failed with exception: {e}")
        return (1, str(workspace_path) if 'workspace_path' in dir() else None)


def main():
    """Main entry point for Part 1."""
    if len(sys.argv) != 3:
        print("Usage: python -m automation_script.part1_build_and_base <PR_URL> <OUTPUT_DATASET_ROOT>")
        print("")
        print("Arguments:")
        print("  PR_URL              : Full URL to the pull request")
        print("  OUTPUT_DATASET_ROOT : Root directory for all output datasets")
        print("")
        print("Example:")
        print("  python -m automation_script.part1_build_and_base \\")
        print("    https://github.com/angular/angular.js/pull/16915 \\")
        print("    ./Output_dataset")
        print("")
        print("Output Structure:")
        print("  Output_dataset/")
        print("    └── angular-angular.js_pr_16915/")
        print("        ├── artifacts/")
        print("        ├── docker_images/")
        print("        ├── logs/")
        print("        ├── metadata/")
        print("        ├── patches/")
        print("        └── results/")
        sys.exit(1)

    pr_url = sys.argv[1]
    output_dataset_root = sys.argv[2]

    exit_code, workspace_path = run_part1(pr_url, output_dataset_root)
    if workspace_path:
        print(f"Workspace: {workspace_path}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
