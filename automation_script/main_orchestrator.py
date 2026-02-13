#!/usr/bin/env python3
"""
PR Evaluation Automation Script

This script evaluates GitHub Pull Requests by:
1. Cloning the repository and fetching PR refs
2. Building an immutable Docker image at the BASE commit
3. Running tests at BASE state (no patches)
4. Generating and applying patches
5. Running tests with patches applied
6. Categorizing tests (FAIL_TO_PASS, PASS_TO_PASS)
7. Generating metadata and artifacts

Usage:
  python -m automation_script.main_orchestrator \\
    https://github.com/owner/repo/pull/NUMBER \\
    /path/to/output

  # Override auto-detected settings
  python -m automation_script.main_orchestrator \\
    --language rust \\
    --test-cmd "cargo test" \\
    https://github.com/owner/repo/pull/123 \\
    /path/to/output

  # Use shallow clone for faster processing
  python -m automation_script.main_orchestrator \\
    --shallow-clone \\
    https://github.com/owner/repo/pull/123 \\
    /path/to/output
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from .config import (
    ARTIFACTS_DIR, DOCKER_IMAGES_DIR, PATCHES_DIR, METADATA_DIR, LOGS_DIR,
    DOCKER_TIMEOUT_RUN, UNIFIED_LOG_FILE, TestResult
)
from .git_wrappers import clone_repository, fetch_pr_refs, get_pr_head_commit
from .git_operations import (
    detect_target_branch, get_base_commit, checkout_commit, parse_pr_url, run_command
)
from .docker_builder_new import build_docker_image, save_and_compress_image
from .docker_runner import (
    run_base_tests, run_patched_tests, run_test_patch_only_tests, verify_patch_applies
)
from .docker_healing import (
    detect_docker_build_error_type, detect_test_error_type,
    should_retry_docker_build, should_retry_test_execution,
    apply_docker_build_healing, apply_test_execution_healing,
    analyze_test_stability, is_zero_tests_error
)
from .language_detection import detect_language_and_test_command
from .environment import detect_language, detect_test_command
from .test_results import categorize_tests
from .metadata_generator import (
    generate_metadata, validate_artifacts,
    generate_test_patch, generate_code_patch
)
from .cleanup import cleanup_workspace, safe_rmtree
from .collect_29_fields import integrate_29_fields_collection


# =============================================================================
# Configuration
# =============================================================================

MAX_DOCKER_BUILD_RETRIES = 3
MAX_TEST_EXECUTION_RETRIES = 3


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging(workspace_root: Path) -> logging.Logger:
    """Set up unified workflow logging."""
    logger = logging.getLogger("pr_eval")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    
    log_format = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    )

    # Console handler (INFO level)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(log_format)
    logger.addHandler(ch)
    
    # File handler (DEBUG level)
    logs_dir = Path(workspace_root) / LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / UNIFIED_LOG_FILE
    
    fh = logging.FileHandler(log_file, mode='a')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(log_format)
    logger.addHandler(fh)

    return logger


# =============================================================================
# Docker Build with Retry
# =============================================================================

def build_docker_image_with_retry(
    repo_path: Path,
    base_commit: str,
    language: str,
    logger: logging.Logger,
    pr_number: Optional[int] = None,
    repo_full_name: Optional[str] = None
) -> Optional[str]:
    """Build Docker image with retry logic and healing."""
    logger.info("Building Docker image with retry/healing logic...")
    logger.info(f"Max retries: {MAX_DOCKER_BUILD_RETRIES}")

    image_tag = None
    last_stderr = ""

    for attempt in range(MAX_DOCKER_BUILD_RETRIES):
        logger.info(f"Docker build attempt {attempt + 1}/{MAX_DOCKER_BUILD_RETRIES}")

        image_tag = build_docker_image(
            repo_path, base_commit, language, logger,
            pr_number=pr_number, repo_full_name=repo_full_name
        )

        if image_tag:
            logger.info(f"✓ Docker build successful on attempt {attempt + 1}")
            return image_tag

        logger.warning(f"✗ Docker build failed on attempt {attempt + 1}")
        error_type = detect_docker_build_error_type(last_stderr)
        logger.info(f"Detected error type: {error_type}")

        if not should_retry_docker_build(attempt, MAX_DOCKER_BUILD_RETRIES - 1, error_type, logger):
            logger.error("Docker build failed and error is not retriable")
            return None

        logger.info("Applying healing strategy...")
        apply_docker_build_healing(
            repo_path=repo_path, language=language,
            attempt=attempt, error_type=error_type, logger=logger
        )

    logger.error(f"Docker build failed after {MAX_DOCKER_BUILD_RETRIES} attempts")
    return None


# =============================================================================
# Test Execution with Retry
# =============================================================================

def run_tests_with_retry(
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
    """Run tests with retry logic and healing."""
    logger.info("Running tests with retry/healing logic...")
    logger.info(f"Max retries: {MAX_TEST_EXECUTION_RETRIES}")

    test_results = []
    timeout = DOCKER_TIMEOUT_RUN
    current_image_tag = image_tag

    for attempt in range(MAX_TEST_EXECUTION_RETRIES):
        logger.info(f"Test execution attempt {attempt + 1}/{MAX_TEST_EXECUTION_RETRIES}")

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

        logger.info(f"Test results: {len(test_result.tests_passed)} passed, "
                   f"{len(test_result.tests_failed)} failed, "
                   f"{len(test_result.tests_skipped)} skipped")

        error_type = detect_test_error_type(test_result)
        if error_type:
            logger.info(f"Detected error type: {error_type}")

        stability = analyze_test_stability(test_results, logger)

        if stability['stable']:
            logger.info(f"✓ Tests are stable after {attempt + 1} attempt(s)")
            return test_result

        if not should_retry_test_execution(
            attempt, MAX_TEST_EXECUTION_RETRIES - 1, test_result, error_type, logger
        ):
            logger.info(f"No retry needed - returning results from attempt {attempt + 1}")
            return test_result

        logger.info("Applying healing strategy...")
        modifications = apply_test_execution_healing(
            repo_path=repo_mount_path, language=language,
            attempt=attempt, error_type=error_type,
            test_result=test_result, logger=logger
        )

        if modifications.get('not_healable'):
            error_reason = modifications.get('error_reason', 'unknown')
            logger.error(f"ERROR IS NOT HEALABLE: {error_reason}")
            return test_result

        if modifications.get('rebuild_docker_image') and base_commit:
            logger.info("Rebuilding Docker image (self-healing)...")
            new_image_tag = build_docker_image(
                repo_path=repo_mount_path, base_commit=base_commit,
                language=language, logger=logger,
                pr_number=pr_number, repo_full_name=repo_full_name,
                no_cache=True
            )
            if new_image_tag:
                current_image_tag = new_image_tag
                logger.info(f"✓ Docker image rebuilt: {current_image_tag}")

        if 'timeout_multiplier' in modifications:
            timeout *= modifications['timeout_multiplier']
            logger.info(f"Increased timeout to {timeout:.0f}s")

    if test_results:
        logger.warning(f"Tests did not stabilize after {MAX_TEST_EXECUTION_RETRIES} attempts")
        return test_results[-1]

    logger.error(f"All {MAX_TEST_EXECUTION_RETRIES} test execution attempts failed")
    return None


# =============================================================================
# Main Workflow
# =============================================================================

def run_pr_evaluation(
    pr_url: str,
    output_root: str,
    overrides: dict = None,
    keep_repo: bool = False,
    cleanup_images: bool = False
) -> int:
    """
    Run the complete PR evaluation workflow.
    
    Args:
        pr_url: GitHub PR URL
        output_root: Root directory for output
        overrides: Optional overrides for language, test_command, etc.
        keep_repo: Keep cloned repository after completion
        cleanup_images: Remove Docker images after completion
        
    Returns:
        Exit code (0 = success, non-zero = failure)
    """
    overrides = overrides or {}
    
    # Parse PR URL
    temp_logger = logging.getLogger("temp")
    temp_logger.setLevel(logging.INFO)
    if not temp_logger.handlers:
        temp_logger.addHandler(logging.StreamHandler(sys.stdout))
    
    pr_info = parse_pr_url(pr_url, temp_logger)
    if not pr_info:
        print(f"ERROR: Failed to parse PR URL: {pr_url}")
        return 1
    
    # Create workspace
    output_path = Path(output_root).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    
    pr_folder_name = f"{pr_info.owner}-{pr_info.repo}_pr_{pr_info.pr_number}".replace("/", "-")
    workspace_path = output_path / pr_folder_name
    workspace_path.mkdir(parents=True, exist_ok=True)
    
    # Setup logging
    logger = setup_logging(workspace_path)
    
    logger.info("=" * 80)
    logger.info("PR EVALUATION AUTOMATION")
    logger.info("=" * 80)
    logger.info(f"PR URL:      {pr_url}")
    logger.info(f"Repository:  {pr_info.owner}/{pr_info.repo}")
    logger.info(f"PR Number:   {pr_info.pr_number}")
    logger.info(f"Workspace:   {workspace_path}")
    if overrides:
        logger.info(f"Overrides:   {list(overrides.keys())}")
    logger.info("=" * 80)
    logger.info("")
    
    repo = f"{pr_info.owner}/{pr_info.repo}"
    pr_number = pr_info.pr_number
    
    try:
        # ========== STEP 1: Clone Repository ==========
        logger.info("[STEP 1/10] Cloning repository")
        logger.info("-" * 40)
        
        repo_path = workspace_path / "repo"
        if repo_path.exists():
            logger.info(f"Removing existing repo at {repo_path}")
            safe_rmtree(repo_path, logger)
        
        use_shallow = overrides.get('shallow_clone', False)
        if use_shallow:
            logger.info("Using shallow clone")
            exit_code, _, stderr = run_command(
                ["git", "clone", "--depth", "1", pr_info.clone_url, str(repo_path)],
                logger=logger, timeout=300
            )
            if exit_code != 0:
                logger.error(f"Shallow clone failed: {stderr}")
                return 1
        else:
            if not clone_repository(pr_info.clone_url, repo_path, logger):
                logger.error("Failed to clone repository")
                return 1
        logger.info("")
        
        # ========== STEP 2: Fetch PR refs ==========
        logger.info("[STEP 2/10] Fetching PR refs and determining commits")
        logger.info("-" * 40)
        
        if use_shallow:
            logger.info("Deepening shallow clone...")
            run_command(
                ["git", "fetch", "--depth", "100", "origin", 
                 f"+refs/pull/{pr_number}/head:refs/remotes/origin/pr/{pr_number}"],
                cwd=repo_path, logger=logger, timeout=120
            )
        
        if not fetch_pr_refs(repo_path, pr_number, logger):
            logger.error("Failed to fetch PR refs")
            return 1
        
        pr_commit = get_pr_head_commit(repo_path, pr_number, logger)
        if not pr_commit:
            logger.error("Failed to get PR head commit")
            return 1
        
        if use_shallow:
            run_command(
                ["git", "fetch", "--deepen", "1000", "origin"],
                cwd=repo_path, logger=logger, timeout=180
            )
        
        target_branch = detect_target_branch(repo_path, logger)
        if not target_branch:
            logger.error("Failed to detect target branch")
            return 1
        
        if 'base_commit' in overrides:
            base_commit = overrides['base_commit']
            logger.info(f"BASE commit (OVERRIDE): {base_commit[:12]}")
        else:
            base_commit = get_base_commit(repo_path, target_branch, pr_commit, pr_info, logger)
            if not base_commit:
                logger.error("Failed to determine base commit")
                return 1
        
        logger.info(f"Target branch: {target_branch}")
        logger.info(f"BASE commit:   {base_commit[:12]}")
        logger.info(f"PR commit:     {pr_commit[:12]}")
        logger.info("")
        
        # ========== STEP 3: Checkout BASE commit ==========
        logger.info("[STEP 3/10] Checking out BASE commit")
        logger.info("-" * 40)
        
        if not checkout_commit(repo_path, base_commit, logger):
            logger.error("Failed to checkout BASE commit")
            return 1
        logger.info("")
        
        # ========== STEP 4: Detect language and test command ==========
        logger.info("[STEP 4/10] Detecting language and test command")
        logger.info("-" * 40)
        
        # Get changed files for accurate detection
        changed_files = []
        exit_code, stdout, _ = run_command(
            ["git", "diff", "--name-only", base_commit, pr_commit],
            cwd=repo_path, logger=logger
        )
        if exit_code == 0 and stdout:
            changed_files = [f.strip() for f in stdout.strip().split('\n') if f.strip()]
            logger.info(f"PR changes {len(changed_files)} files")
        
        repo_full_name = f"{pr_info.owner}/{pr_info.repo}"
        
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
            if language == 'rust' and 'rust_subdir' in overrides:
                rust_subdir = overrides['rust_subdir']
                test_command = f"cargo test --manifest-path {rust_subdir}/Cargo.toml"
            else:
                test_command = detect_test_command(repo_path, language, logger, repo_full_name=repo_full_name, changed_files=changed_files)
            logger.info(f"Test command (detected): {test_command}")
        logger.info("")
        
        # ========== STEP 5: Build Docker image ==========
        logger.info("[STEP 5/10] Building Docker image at BASE commit")
        logger.info("-" * 40)
        
        if 'reuse_image' in overrides:
            image_tag = overrides['reuse_image']
            logger.info(f"⏩ REUSING existing Docker image: {image_tag}")
            exit_code, _, stderr = run_command(
                ["docker", "image", "inspect", image_tag],
                logger=logger, timeout=30
            )
            if exit_code != 0:
                logger.error(f"Specified image does not exist: {image_tag}")
                return 1
        else:
            image_tag = build_docker_image_with_retry(
                repo_path, base_commit, language, logger,
                pr_number=pr_number, repo_full_name=repo_full_name
            )
            if not image_tag:
                logger.error("Failed to build Docker image")
                return 1
        logger.info("")
        
        # ========== STEP 6: Save Docker image ==========
        logger.info("[STEP 6/10] Saving and compressing Docker image")
        logger.info("-" * 40)
        
        docker_images_dir = workspace_path / DOCKER_IMAGES_DIR
        docker_images_dir.mkdir(parents=True, exist_ok=True)
        
        image_uri = save_and_compress_image(
            image_tag, docker_images_dir, base_commit, logger,
            repo_full_name=repo, repo_path=repo_path, save_dockerfile=True
        )
        if not image_uri:
            logger.error("Failed to save Docker image")
            return 1
        logger.info("")
        
        # ========== STEP 7: Run BASE tests ==========
        logger.info("[STEP 7/10] Running BASE tests in container")
        logger.info("-" * 40)
        
        base_artifacts_dir = workspace_path / ARTIFACTS_DIR / "base"
        base_artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        skip_tests = overrides.get('skip_tests', False)
        
        if skip_tests:
            logger.info("⏩ SKIPPING test execution (--skip-tests)")
            base_result = TestResult(
                success=True, exit_code=0,
                stdout="Tests skipped", stderr="",
                duration=0.0, tests_passed=[], tests_failed=[], tests_skipped=[]
            )
            # Save placeholder result
            with open(base_artifacts_dir / "result.json", 'w') as f:
                json.dump({
                    "success": True, "exit_code": 0, "duration": 0.0,
                    "tests_passed": [], "tests_failed": [], "tests_skipped": []
                }, f, indent=2)
        else:
            base_result = run_tests_with_retry(
                image_tag=image_tag, workspace_path=workspace_path,
                repo_mount_path=repo_path, test_command=test_command,
                language=language, logger=logger,
                base_commit=base_commit, pr_number=pr_number, repo_full_name=repo_full_name
            )
            
            if base_result is None:
                logger.error("BASE tests did not produce results")
                return 1
            
            # Check for zero tests
            total_tests = len(base_result.tests_passed) + len(base_result.tests_failed) + len(base_result.tests_skipped)
            if total_tests == 0 and base_result.exit_code != 0:
                logger.error("")
                logger.error("=" * 80)
                logger.error("FAILED: Zero tests ran at BASE commit")
                logger.error("=" * 80)
                logger.error("No tests were executed. This indicates a build or environment failure.")
                logger.error(f"Exit code: {base_result.exit_code}")
                logger.error("")
                logger.error("TROUBLESHOOTING:")
                logger.error(f"  1. Check test command: {test_command}")
                logger.error("  2. Try overriding with --test-cmd '<command>'")
                logger.error("  3. Check Docker build logs for clues")
                logger.error("=" * 80)
                return 1
        
        logger.info(f"BASE tests: {len(base_result.tests_passed)} passed, "
                   f"{len(base_result.tests_failed)} failed, "
                   f"{len(base_result.tests_skipped)} skipped")
        logger.info("")
        
        # ========== STEP 8: Generate and apply patches ==========
        logger.info("[STEP 8/10] Generating patch files")
        logger.info("-" * 40)
        
        patches_dir = workspace_path / PATCHES_DIR
        patches_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate full patch
        full_patch_file = patches_dir / "pr.patch"
        exit_code, stdout, stderr = run_command(
            ["git", "diff", base_commit, pr_commit],
            cwd=repo_path, logger=logger
        )
        if exit_code != 0:
            logger.error(f"Failed to generate patch: {stderr}")
            return 1
        
        with open(full_patch_file, 'w') as f:
            f.write(stdout)
        logger.info(f"Full patch: {len(stdout)} bytes → pr.patch")
        
        # Generate test-only patch
        test_patch_file = patches_dir / "test.patch"
        test_patch_content = generate_test_patch(repo_path, base_commit, pr_commit, logger)
        with open(test_patch_file, 'w') as f:
            f.write(test_patch_content or "")
        logger.info(f"Test patch: {len(test_patch_content or '')} bytes → test.patch")
        
        # Generate code-only patch
        code_patch_file = patches_dir / "code.patch"
        code_patch_content = generate_code_patch(repo_path, base_commit, pr_commit, logger)
        with open(code_patch_file, 'w') as f:
            f.write(code_patch_content or "")
        logger.info(f"Code patch: {len(code_patch_content or '')} bytes → code.patch")
        
        # Verify patch applies
        success, error = verify_patch_applies(image_tag, workspace_path, repo_path, full_patch_file, logger)
        if not success:
            logger.error(f"Patch does not apply cleanly: {error}")
            return 1
        logger.info("✓ Patch applies cleanly")
        logger.info("")
        
        # ========== STEP 9: Run patched tests ==========
        logger.info("[STEP 9/10] Running tests with patches applied")
        logger.info("-" * 40)
        
        # Run 2: TEST_PATCH only (if test patch exists)
        test_patch_only_result = None
        if test_patch_file.stat().st_size > 0:
            logger.info("Running TEST_PATCH only (new tests should FAIL)...")
            test_patch_only_result = run_test_patch_only_tests(
                image_tag=image_tag, workspace_path=workspace_path,
                repo_mount_path=repo_path, test_patch_path=test_patch_file,
                test_command=test_command, logger=logger
            )
        else:
            logger.info("⊘ Skipping TEST_PATCH run: no test files changed")
        
        # Run 3: FULL PATCH
        logger.info("Running FULL PATCH (patched tests should PASS)...")
        pr_artifacts_dir = workspace_path / ARTIFACTS_DIR / "pr"
        pr_artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        pr_result = run_patched_tests(
            image_tag=image_tag, workspace_path=workspace_path,
            repo_mount_path=repo_path, patch_path=full_patch_file,
            test_command=test_command, logger=logger
        )
        
        if pr_result:
            logger.info(f"PATCHED tests: {len(pr_result.tests_passed)} passed, "
                       f"{len(pr_result.tests_failed)} failed, "
                       f"{len(pr_result.tests_skipped)} skipped")
            
            # Check for zero tests in patched run
            pr_total = len(pr_result.tests_passed) + len(pr_result.tests_failed) + len(pr_result.tests_skipped)
            if pr_total == 0 and pr_result.exit_code != 0:
                logger.error("")
                logger.error("=" * 80)
                logger.error("FAILED: Zero tests ran with patches applied")
                logger.error("=" * 80)
                logger.error("The patched test run failed before any tests could execute.")
                logger.error(f"Exit code: {pr_result.exit_code}")
                logger.error("=" * 80)
                return 1
        else:
            logger.warning("PATCHED tests did not produce results")
        logger.info("")
        
        # ========== STEP 10: Categorize and generate metadata ==========
        logger.info("[STEP 10/10] Categorizing tests and generating metadata")
        logger.info("-" * 40)
        
        # Load BASE result
        base_result_file = base_artifacts_dir / "result.json"
        with open(base_result_file, 'r') as f:
            base_result_full = json.load(f)
        
        # Prepare PR result dict
        pr_result_dict = {
            "tests_passed": pr_result.tests_passed if pr_result else [],
            "tests_failed": pr_result.tests_failed if pr_result else [],
            "tests_skipped": pr_result.tests_skipped if pr_result else [],
            "success": pr_result.success if pr_result else False,
            "exit_code": pr_result.exit_code if pr_result else -1
        }
        
        # Prepare test patch only result dict
        test_patch_only_dict = None
        if test_patch_only_result:
            test_patch_only_dict = {
                "tests_passed": test_patch_only_result.tests_passed,
                "tests_failed": test_patch_only_result.tests_failed,
                "tests_skipped": test_patch_only_result.tests_skipped
            }
        
        # Load patch content for filtering
        with open(full_patch_file, 'r') as f:
            patch_content = f.read()
        
        # Categorize tests
        fail_to_pass, pass_to_pass = categorize_tests(
            base_result_full, pr_result_dict, logger,
            patch_content=patch_content, language=language,
            test_patch_only_result=test_patch_only_dict
        )
        
        logger.info(f"FAIL_TO_PASS: {len(fail_to_pass)} tests")
        logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests")
        
        # Generate metadata
        metadata_dir = workspace_path / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)
        
        metadata = generate_metadata(
            repo=repo, base_commit=base_commit, pr_commit=pr_commit,
            language=language, test_command=test_command,
            fail_to_pass=fail_to_pass, pass_to_pass=pass_to_pass,
            image_uri=image_uri, patch_file=full_patch_file,
            repo_path=repo_path, metadata_dir=metadata_dir, logger=logger,
            pr_number=pr_number, pr_url=pr_url
        )
        
        if not metadata:
            logger.error("Failed to generate metadata")
            return 1
        
        # Collect 29-field data
        output_29_fields_dir = workspace_path.parent.parent / "29_fields"
        integrate_29_fields_collection(
            workspace_path=workspace_path, logger=logger,
            output_dir=output_29_fields_dir, fetch_github_metadata=True
        )
        
        # Validate artifacts
        validate_artifacts(base_artifacts_dir, logger)
        validate_artifacts(pr_artifacts_dir, logger)
        logger.info("")
        
        # ========== Cleanup ==========
        logger.info("Cleaning up workspace...")
        cleanup_workspace(workspace_path, keep_repo=keep_repo, cleanup_images=cleanup_images, logger=logger)
        
        # ========== Summary ==========
        logger.info("")
        logger.info("=" * 80)
        logger.info("✓ PR EVALUATION COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        logger.info(f"Workspace:     {workspace_path}")
        logger.info(f"Docker image:  {image_tag}")
        logger.info(f"FAIL_TO_PASS:  {len(fail_to_pass)} tests")
        logger.info(f"PASS_TO_PASS:  {len(pass_to_pass)} tests")
        logger.info("")
        logger.info("Output Structure:")
        logger.info(f"  {pr_folder_name}/")
        logger.info("    ├── artifacts/")
        logger.info("    ├── docker_images/")
        logger.info("    ├── logs/")
        logger.info("    ├── metadata/")
        logger.info("    ├── patches/")
        logger.info("    └── 29_fields/")
        logger.info("=" * 80)
        
        return 0
        
    except Exception as e:
        logger.exception(f"PR evaluation failed with exception: {e}")
        return 1


# =============================================================================
# Argument Parsing
# =============================================================================

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='PR Evaluation Automation Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  %(prog)s https://github.com/owner/repo/pull/123 ./output

  # Override language detection
  %(prog)s --language rust https://github.com/owner/repo/pull/123 ./output

  # Override test command
  %(prog)s --test-cmd "cargo test" https://github.com/owner/repo/pull/123 ./output

  # Fast mode: shallow clone
  %(prog)s --shallow-clone https://github.com/owner/repo/pull/123 ./output

  # Reuse existing Docker image
  %(prog)s --reuse-image pr-eval:base-abc123 https://github.com/owner/repo/pull/123 ./output

Supported Languages:
  python, javascript, typescript, go, rust, java, csharp, ruby
        """
    )

    # Override options
    override_group = parser.add_argument_group('Detection Overrides')
    override_group.add_argument(
        '--language', '-l',
        type=str,
        choices=['python', 'javascript', 'typescript', 'go', 'rust', 'java', 'csharp', 'ruby'],
        help='Override detected language'
    )
    override_group.add_argument(
        '--test-cmd', '-t',
        type=str,
        dest='test_command',
        help='Override detected test command'
    )
    override_group.add_argument(
        '--rust-subdir',
        type=str,
        help='For Rust: subdirectory containing Cargo.toml'
    )
    override_group.add_argument(
        '--base-commit',
        type=str,
        metavar='SHA',
        help='Override auto-detected base commit'
    )

    # Performance options
    perf_group = parser.add_argument_group('Performance Options')
    perf_group.add_argument(
        '--shallow-clone',
        action='store_true',
        help='Use shallow clone (faster)'
    )
    perf_group.add_argument(
        '--reuse-image',
        type=str,
        metavar='IMAGE_TAG',
        help='Reuse existing Docker image'
    )
    perf_group.add_argument(
        '--skip-tests',
        action='store_true',
        help='Skip running tests'
    )

    # Cleanup options
    cleanup_group = parser.add_argument_group('Cleanup Options')
    cleanup_group.add_argument(
        '--keep-repo',
        action='store_true',
        help='Keep cloned repository after completion'
    )
    cleanup_group.add_argument(
        '--cleanup-images',
        action='store_true',
        help='Remove Docker images after completion'
    )

    # Positional arguments
    parser.add_argument('pr_url', help='GitHub PR URL')
    parser.add_argument('output', help='Output directory')

    args = parser.parse_args()

    # Auto-set language for rust-subdir
    if args.rust_subdir and not args.language:
        args.language = 'rust'

    return args


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point."""
    args = parse_arguments()
    
    # Build overrides
    overrides = {}
    if args.language:
        overrides['language'] = args.language
    if args.test_command:
        overrides['test_command'] = args.test_command
    if args.rust_subdir:
        overrides['rust_subdir'] = args.rust_subdir
    if args.shallow_clone:
        overrides['shallow_clone'] = True
    if args.reuse_image:
        overrides['reuse_image'] = args.reuse_image
    if args.skip_tests:
        overrides['skip_tests'] = True
    if args.base_commit:
        overrides['base_commit'] = args.base_commit
    
    # Run evaluation
    exit_code = run_pr_evaluation(
        pr_url=args.pr_url,
        output_root=args.output,
        overrides=overrides,
        keep_repo=args.keep_repo,
        cleanup_images=args.cleanup_images
    )
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
