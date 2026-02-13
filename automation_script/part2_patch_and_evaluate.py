#!/usr/bin/env python3
"""
PART 2: Patch Application and Evaluation (Three-Run Sequence)

This script handles:
- Loading state from Part 1
- Generating separate test_patch and code_patch files
- Running the THREE-RUN SEQUENCE:
  - Run 1: BASE tests (from Part 1)
  - Run 2: TEST_PATCH only (new tests must FAIL)
  - Run 3: FULL PATCH (new tests must PASS, no regressions)
- Categorizing tests using proper F2P/P2P validation:
  - F2P: FAIL in Run 2 AND PASS in Run 3
  - P2P: PASS in Run 1 AND PASS in Run 3 (excluding F2P)
- Generating metadata
- Validation

Input: state.json from Part 1
Output: Patch files + test results (3 runs) + metadata.json
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import subprocess

from .config import (
    ARTIFACTS_DIR, PATCHES_DIR, METADATA_DIR, LOGS_DIR,
    DOCKER_MEMORY_LIMIT, DOCKER_CPU_LIMIT, DOCKER_TIMEOUT_RUN
)
from .git_operations import run_command
from .docker_runner import run_patched_tests, run_test_patch_only_tests, verify_patch_applies
from .test_results import categorize_tests
from .metadata_generator import (
    generate_metadata, validate_artifacts,
    generate_test_patch, generate_code_patch
)
from .cleanup import cleanup_workspace
from .collect_29_fields import integrate_29_fields_collection


def setup_logging(workspace_root: Path, external_logger: logging.Logger = None) -> logging.Logger:
    """
    Set up logging for Part 2.
    
    Args:
        workspace_root: Path to the workspace directory
        external_logger: Optional external logger (from orchestrator) for unified logging
        
    Returns:
        Logger instance to use for Part 2 operations
    """
    # If external logger provided (unified logging mode), use it
    if external_logger is not None:
        # Create a child logger that inherits from the external logger
        logger = logging.getLogger("part2")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.parent = external_logger
        logger.propagate = True
        return logger
    
    # Standalone mode: create own file handler (for backward compatibility)
    logs_dir = workspace_root / LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_file = logs_dir / "part2_patch_and_evaluate.log"

    logger = logging.getLogger("part2")
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


def load_state(workspace_root: Path, logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """Load state.json from Part 1."""
    state_file = workspace_root / "state.json"

    if not state_file.exists():
        logger.error(f"State file not found: {state_file}")
        logger.error("You must run Part 1 first!")
        return None

    try:
        with open(state_file, 'r') as f:
            state = json.load(f)

        logger.info(f"Loaded state from {state_file}")
        logger.info(f"State summary:")
        logger.info(f"  - repo: {state.get('repo')}")
        logger.info(f"  - pr_number: {state.get('pr_number')}")
        logger.info(f"  - base_commit: {state.get('base_commit')}")
        logger.info(f"  - pr_commit: {state.get('pr_commit')}")
        logger.info(f"  - language: {state.get('language')}")
        logger.info(f"  - docker_image: {state.get('docker_image')}")

        # Validate required fields
        required_fields = [
            'repo', 'pr_number', 'base_commit', 'pr_commit',
            'language', 'test_command', 'docker_image', 'image_uri',
            'repo_path', 'workspace_path', 'base_artifacts_dir'
        ]

        missing = [f for f in required_fields if f not in state]
        if missing:
            logger.error(f"Missing required fields in state: {missing}")
            return None

        return state

    except Exception as e:
        logger.exception(f"Failed to load state: {e}")
        return None


def generate_patch_files(
    repo_path: Path,
    base_commit: str,
    pr_commit: str,
    patches_dir: Path,
    logger: logging.Logger
) -> Dict[str, Optional[Path]]:
    """
    Generate all patch files for the three-run sequence.
    
    Creates:
    - pr.patch: Full patch (test_patch + code_patch) for Run 3
    - test.patch: Test files only for Run 2
    - code.patch: Code files only (for reference)
    
    Args:
        repo_path: Path to repository
        base_commit: BASE commit SHA
        pr_commit: PR commit SHA
        patches_dir: Directory to save patches
        logger: Logger instance
        
    Returns:
        Dict with keys 'full', 'test', 'code' mapping to patch file paths (or None if failed)
    """
    patches_dir.mkdir(parents=True, exist_ok=True)
    result = {'full': None, 'test': None, 'code': None}

    logger.info(f"Generating patches: {base_commit[:12]}..{pr_commit[:12]}")
    logger.info("-" * 40)

    # 1. Generate FULL patch (pr.patch) - for Run 3
    logger.info("Generating full patch (pr.patch)...")
    exit_code, stdout, stderr = run_command(
        ["git", "diff", "--binary", "--full-index", base_commit, pr_commit],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Failed to generate full patch: {stderr}")
        return result

    full_patch_file = patches_dir / "pr.patch"
    with open(full_patch_file, 'w') as f:
        f.write(stdout)
    result['full'] = full_patch_file
    logger.info(f"  Full patch: {full_patch_file.stat().st_size} bytes → {full_patch_file.name}")

    # 2. Generate TEST patch (test.patch) - for Run 2
    logger.info("Generating test-only patch (test.patch)...")
    test_patch_content = generate_test_patch(repo_path, base_commit, pr_commit, logger)
    test_patch_file = patches_dir / "test.patch"
    with open(test_patch_file, 'w') as f:
        f.write(test_patch_content)
    result['test'] = test_patch_file
    logger.info(f"  Test patch: {test_patch_file.stat().st_size} bytes → {test_patch_file.name}")

    # 3. Generate CODE patch (code.patch) - for reference/metadata
    logger.info("Generating code-only patch (code.patch)...")
    code_patch_content = generate_code_patch(repo_path, base_commit, pr_commit, logger)
    code_patch_file = patches_dir / "code.patch"
    with open(code_patch_file, 'w') as f:
        f.write(code_patch_content)
    result['code'] = code_patch_file
    logger.info(f"  Code patch: {code_patch_file.stat().st_size} bytes → {code_patch_file.name}")

    logger.info("-" * 40)
    logger.info(f"Total: {full_patch_file.stat().st_size} bytes full = "
               f"{test_patch_file.stat().st_size} bytes test + "
               f"{code_patch_file.stat().st_size} bytes code")

    return result


def generate_patch_file(
    repo_path: Path,
    base_commit: str,
    pr_commit: str,
    patches_dir: Path,
    logger: logging.Logger
) -> Optional[Path]:
    """
    Generate full patch file from BASE to PR commit (legacy function).
    
    Kept for backward compatibility. Use generate_patch_files() for three-run sequence.
    """
    patches_dir.mkdir(parents=True, exist_ok=True)
    patch_file = patches_dir / "pr.patch"

    logger.info(f"Generating patch: {base_commit[:12]}..{pr_commit[:12]}")

    # Use --binary and --full-index for proper binary patch support
    # --binary: includes binary file content as base85-encoded data
    # --full-index: uses full 40-char SHA hashes (required for 'git apply' to work on binary files)
    exit_code, stdout, stderr = run_command(
        ["git", "diff", "--binary", "--full-index", base_commit, pr_commit],
        cwd=repo_path,
        logger=logger
    )

    if exit_code != 0:
        logger.error(f"Failed to generate patch: {stderr}")
        return None

    with open(patch_file, 'w') as f:
        f.write(stdout)

    patch_size = patch_file.stat().st_size
    logger.info(f"Patch saved: {patch_size} bytes → {patch_file}")

    return patch_file


def run_part2(workspace_root: str, keep_repo: bool = False, cleanup_images: bool = False, logger: logging.Logger = None) -> int:
    """
    Execute Part 2: Apply patch and evaluate PR using the THREE-RUN SEQUENCE.

    The three-run sequence:
    - Run 1: BASE tests (from Part 1) - no patches applied
    - Run 2: TEST_PATCH only - new tests should FAIL
    - Run 3: FULL PATCH - new tests should PASS, no regressions

    F2P validation: Test must FAIL in Run 2 AND PASS in Run 3
    P2P: Tests that PASS in Run 1 AND PASS in Run 3 (excluding F2P)

    Args:
        workspace_root: Path to workspace directory
        keep_repo: If True, keep the cloned repository (default: False)
        cleanup_images: If True, remove Docker images after completion (default: False)
        logger: Optional external logger for unified logging

    Returns:
        0 on success, non-zero on failure
    """
    workspace_path = Path(workspace_root).resolve()

    if not workspace_path.exists():
        print(f"ERROR: Workspace directory not found: {workspace_path}")
        print("You must run Part 1 first!")
        return 1

    # Set up logging - use external logger if provided (unified logging)
    logger = setup_logging(workspace_path, external_logger=logger)

    logger.info("=" * 80)
    logger.info("PART 2: PATCH APPLICATION AND EVALUATION (THREE-RUN SEQUENCE)")
    logger.info("=" * 80)
    logger.info(f"Workspace:   {workspace_path}")
    logger.info("")
    logger.info("THREE-RUN SEQUENCE:")
    logger.info("  Run 1: BASE state (no patches) - from Part 1")
    logger.info("  Run 2: TEST_PATCH only - new tests MUST FAIL")
    logger.info("  Run 3: FULL PATCH - new tests must PASS, no regressions")
    logger.info("")
    logger.info("CATEGORIZATION:")
    logger.info("  F2P = FAIL in Run 2 AND PASS in Run 3")
    logger.info("  P2P = PASS in Run 1 AND PASS in Run 3 (excluding F2P)")
    logger.info("=" * 80)
    logger.info("")

    try:
        # ========== STEP 1: Load state from Part 1 ==========
        logger.info("[STEP 1/9] Loading state from Part 1 (includes Run 1 results)")
        logger.info("-" * 40)

        state = load_state(workspace_path, logger)
        if not state:
            logger.error("Failed to load state from Part 1")
            return 1

        # Extract state variables
        repo = state['repo']
        pr_number = state['pr_number']
        base_commit = state['base_commit']
        pr_commit = state['pr_commit']
        target_branch = state['target_branch']
        language = state['language']
        test_command = state['test_command']
        docker_image = state['docker_image']
        image_uri = state['image_uri']
        repo_path = Path(state['repo_path'])
        base_artifacts_dir = Path(state['base_artifacts_dir'])
        base_result = state.get('base_result')

        logger.info("")

        # ========== STEP 2: Generate all patch files ==========
        logger.info("[STEP 2/9] Generating patch files (full, test, code)")
        logger.info("-" * 40)

        patches_dir = workspace_path / PATCHES_DIR
        patch_files = generate_patch_files(
            repo_path, base_commit, pr_commit, patches_dir, logger
        )
        
        if not patch_files.get('full'):
            logger.error("Failed to generate full patch file")
            return 1
        
        full_patch_file = patch_files['full']
        test_patch_file = patch_files['test']
        code_patch_file = patch_files['code']
        logger.info("")

        # ========== STEP 3: Verify patches apply cleanly ==========
        logger.info("[STEP 3/9] Verifying patches apply cleanly")
        logger.info("-" * 40)

        # Verify full patch
        patch_valid, patch_error = verify_patch_applies(
            docker_image, workspace_path, repo_path, full_patch_file, logger, 
            patch_name="pr.patch"
        )
        if not patch_valid:
            logger.error(f"Full patch verification failed: {patch_error}")
            return 1
        logger.info("✓ Full patch (pr.patch) applies cleanly")

        # Verify test patch (if not empty)
        if test_patch_file.stat().st_size > 0:
            test_patch_valid, test_patch_error = verify_patch_applies(
                docker_image, workspace_path, repo_path, test_patch_file, logger,
                patch_name="test.patch"
            )
            if not test_patch_valid:
                logger.error(f"Test patch verification failed: {test_patch_error}")
                return 1
            logger.info("✓ Test patch (test.patch) applies cleanly")
        else:
            logger.info("⊘ Test patch is empty (no test files changed)")
        logger.info("")

        # ========== STEP 4: Run 2 - TEST_PATCH only tests ==========
        logger.info("[STEP 4/9] RUN 2: Running tests with TEST_PATCH only")
        logger.info("-" * 40)
        logger.info("Purpose: Validate that new tests FAIL without the solution")
        logger.info("Container: ephemeral, spawned from BASE image")
        logger.info("Patch: test.patch only (no code changes)")

        test_patch_only_artifacts_dir = workspace_path / ARTIFACTS_DIR / "test_patch_only"
        test_patch_only_artifacts_dir.mkdir(parents=True, exist_ok=True)

        test_patch_only_result = None
        test_patch_only_result_dict = None

        if test_patch_file.stat().st_size > 0:
            test_patch_only_result = run_test_patch_only_tests(
                image_tag=docker_image,
                workspace_path=workspace_path,
                repo_mount_path=repo_path,
                test_patch_path=test_patch_file,
                test_command=test_command,
                logger=logger
            )

            if test_patch_only_result is None:
                logger.warning("Run 2 (TEST_PATCH only) did not produce results")
                # Try to load from artifacts
                run2_result_file = test_patch_only_artifacts_dir / "result.json"
                if run2_result_file.exists():
                    with open(run2_result_file, 'r') as f:
                        test_patch_only_result_dict = json.load(f)
                    logger.info(f"Loaded Run 2 results from {run2_result_file}")
            else:
                logger.info(f"Run 2 results: {len(test_patch_only_result.tests_passed)} passed, "
                           f"{len(test_patch_only_result.tests_failed)} failed, "
                           f"{len(test_patch_only_result.tests_skipped)} skipped")
                test_patch_only_result_dict = {
                    "tests_passed": test_patch_only_result.tests_passed,
                    "tests_failed": test_patch_only_result.tests_failed,
                    "tests_skipped": test_patch_only_result.tests_skipped,
                    "success": test_patch_only_result.success,
                    "exit_code": test_patch_only_result.exit_code
                }
        else:
            logger.info("⊘ Skipping Run 2: test.patch is empty (no test files changed)")
            logger.info("  F2P validation will use fallback logic")
        logger.info("")

        # ========== STEP 5: Run 3 - FULL PATCH tests ==========
        logger.info("[STEP 5/9] RUN 3: Running tests with FULL PATCH")
        logger.info("-" * 40)
        logger.info("Purpose: Validate that new tests PASS with solution, no regressions")
        logger.info("Container: ephemeral, spawned from BASE image")
        logger.info("Patch: pr.patch (full patch = test + code changes)")

        pr_artifacts_dir = workspace_path / ARTIFACTS_DIR / "pr"
        pr_artifacts_dir.mkdir(parents=True, exist_ok=True)

        pr_result = run_patched_tests(
            image_tag=docker_image,
            workspace_path=workspace_path,
            repo_mount_path=repo_path,
            patch_path=full_patch_file,
            test_command=test_command,
            logger=logger
        )

        if pr_result is None:
            logger.error("Run 3 (FULL PATCH) did not produce results")
            # Try to load from artifacts (container may have written result.json before exiting)
            pr_result_dict = None
        else:
            logger.info(f"Run 3 results: {len(pr_result.tests_passed)} passed, "
                       f"{len(pr_result.tests_failed)} failed, "
                       f"{len(pr_result.tests_skipped)} skipped")
            pr_result_dict = {
                "tests_passed": pr_result.tests_passed,
                "tests_failed": pr_result.tests_failed,
                "tests_skipped": pr_result.tests_skipped,
                "success": pr_result.success,
                "exit_code": pr_result.exit_code
            }
        logger.info("")

        # ========== STEP 6: Categorize tests using three-run sequence ==========
        logger.info("[STEP 6/9] Categorizing tests (THREE-RUN SEQUENCE)")
        logger.info("-" * 40)

        # Load full BASE result from artifacts (Run 1 from Part 1)
        base_result_file = base_artifacts_dir / "result.json"
        if not base_result_file.exists():
            logger.error(f"Run 1 (BASE) result file not found: {base_result_file}")
            logger.error("Part 1 may have failed to run BASE tests or used a different workspace path.")
            return 1
        logger.info(f"Loading Run 1 (BASE) results from {base_result_file}")
        with open(base_result_file, 'r') as f:
            base_result_full = json.load(f)

        # Use PR result from run or load from artifacts if container wrote result.json
        if pr_result_dict is None:
            pr_result_file = pr_artifacts_dir / "result.json"
            if pr_result_file.exists():
                with open(pr_result_file, 'r') as f:
                    pr_result_dict = json.load(f)
                logger.info(f"Loaded Run 3 (FULL PATCH) results from {pr_result_file}")
            else:
                pr_result_dict = {
                    "tests_passed": [], "tests_failed": [], "tests_skipped": [],
                    "success": False, "exit_code": -1
                }
                logger.warning("No Run 3 result file; using empty result for categorization")

        # Read patch content for filtering PASS_TO_PASS to PR-relevant tests only
        patch_content = None
        if full_patch_file and full_patch_file.exists():
            with open(full_patch_file, 'r') as f:
                patch_content = f.read()
            logger.info(f"Loaded patch content for test filtering ({len(patch_content)} bytes)")

        # Use the three-run categorization with Run 2 results
        fail_to_pass, pass_to_pass = categorize_tests(
            base_result_full, pr_result_dict, logger,
            patch_content=patch_content,
            language=language,
            test_patch_only_result=test_patch_only_result_dict
        )

        logger.info(f"FAIL_TO_PASS: {len(fail_to_pass)} tests")
        logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests")

        # Detect environment/setup failure: no tests ran in BASE or PR
        base_ran = len(base_result_full.get("tests_passed", [])) + len(base_result_full.get("tests_failed", []))
        pr_ran = len(pr_result_dict.get("tests_passed", [])) + len(pr_result_dict.get("tests_failed", []))
        if base_ran == 0 or pr_ran == 0:
            logger.warning(
                "⚠️  No test results to categorize: Run 1 ran %d tests, Run 3 ran %d tests. "
                "This usually means the test command failed before any tests ran (e.g. missing deps, wrong command).",
                base_ran, pr_ran
            )
            
            # Fail if Run 3 (FULL PATCH) produced zero tests with non-zero exit code
            pr_exit_code = pr_result_dict.get("exit_code", -1)
            if pr_ran == 0 and pr_exit_code != 0:
                logger.error("")
                logger.error("=" * 80)
                logger.error("PART 2 FAILED: Zero tests ran in Run 3 (FULL PATCH)")
                logger.error("=" * 80)
                logger.error("The patched test run failed before any tests could execute.")
                logger.error(f"Exit code: {pr_exit_code}")
                logger.error("")
                logger.error("This usually means:")
                logger.error("  1. The patch introduces build errors")
                logger.error("  2. Dependencies are missing or incompatible")
                logger.error("  3. The test command is incorrect")
                logger.error("")
                logger.error("Check the artifacts/pr/ directory for detailed logs.")
                logger.error("=" * 80)
                return 1
                
        if len(fail_to_pass) == 0:
            logger.warning("⚠️  No FAIL_TO_PASS tests found")
        logger.info("")

        # ========== STEP 7: Generate metadata ==========
        logger.info("[STEP 7/9] Generating metadata")
        logger.info("-" * 40)

        metadata_dir = workspace_path / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)

        # Reconstruct PR URL from state
        pr_url = state.get('pr_url', f"https://github.com/{repo}/pull/{pr_number}")

        metadata = generate_metadata(
            repo=repo,
            base_commit=base_commit,
            pr_commit=pr_commit,
            language=language,
            test_command=test_command,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            image_uri=image_uri,
            patch_file=full_patch_file,
            repo_path=repo_path,
            metadata_dir=metadata_dir,
            logger=logger,
            pr_number=pr_number,
            pr_url=pr_url
        )

        if not metadata:
            logger.error("Failed to generate metadata")
            return 1
        logger.info("")

        # ========== STEP 8: Collect 29-field data ==========
        logger.info("[STEP 8/9] Collecting 29-field task template data")
        logger.info("-" * 40)
        
        # Determine the 29_fields output directory
        output_29_fields_dir = workspace_path.parent.parent / "29_fields"
        
        # Collect and save 29-field data
        fields_collected = integrate_29_fields_collection(
            workspace_path=workspace_path,
            logger=logger,
            output_dir=output_29_fields_dir,
            fetch_github_metadata=True
        )
        
        if fields_collected:
            logger.info("✓ 29-field data collected successfully")
        else:
            logger.warning("⚠️  29-field collection had issues (non-fatal)")
        logger.info("")

        # ========== STEP 9: Validate outputs ==========
        logger.info("[STEP 9/9] Validating outputs")
        logger.info("-" * 40)

        validation_passed = True

        # Validate Run 1 (BASE) artifacts
        logger.info(f"Validating Run 1 (BASE) artifacts in {base_artifacts_dir}")
        if not validate_artifacts(base_artifacts_dir, logger):
            logger.error("Run 1 (BASE) artifacts validation failed")
            validation_passed = False

        # Validate Run 2 (TEST_PATCH_ONLY) artifacts (if test patch was non-empty)
        if test_patch_file.stat().st_size > 0:
            logger.info(f"Validating Run 2 (TEST_PATCH_ONLY) artifacts in {test_patch_only_artifacts_dir}")
            if not validate_artifacts(test_patch_only_artifacts_dir, logger):
                logger.warning("Run 2 (TEST_PATCH_ONLY) artifacts validation failed (non-fatal)")
                # Don't fail validation for Run 2 - it's a validation run, results may be empty

        # Validate Run 3 (FULL PATCH) artifacts
        logger.info(f"Validating Run 3 (FULL PATCH) artifacts in {pr_artifacts_dir}")
        if not validate_artifacts(pr_artifacts_dir, logger):
            logger.error("Run 3 (FULL PATCH) artifacts validation failed")
            validation_passed = False

        # Validate metadata
        logger.info("Validating metadata")
        metadata_file = metadata_dir / "instance.json"
        if not metadata_file.exists():
            logger.error("Metadata file not found")
            validation_passed = False
        else:
            logger.info("Metadata validation passed")

        if not validation_passed:
            logger.warning("⚠️  Some validation checks failed")
        logger.info("")

        # ========== Summary ==========
        logger.info("=" * 80)
        logger.info("PART 2 COMPLETED SUCCESSFULLY (THREE-RUN SEQUENCE)")
        logger.info("=" * 80)
        logger.info("")
        logger.info("THREE-RUN SEQUENCE RESULTS:")
        logger.info(f"  Run 1 (BASE):       {base_artifacts_dir}")
        if test_patch_file.stat().st_size > 0:
            logger.info(f"  Run 2 (TEST_PATCH): {test_patch_only_artifacts_dir}")
        else:
            logger.info(f"  Run 2 (TEST_PATCH): Skipped (no test files changed)")
        logger.info(f"  Run 3 (FULL PATCH): {pr_artifacts_dir}")
        logger.info("")
        logger.info("PATCH FILES:")
        logger.info(f"  Full patch (pr.patch):   {full_patch_file.stat().st_size} bytes")
        logger.info(f"  Test patch (test.patch): {test_patch_file.stat().st_size} bytes")
        logger.info(f"  Code patch (code.patch): {code_patch_file.stat().st_size} bytes")
        logger.info("")
        logger.info("📦 FINAL BENCHMARK ARTIFACTS:")
        logger.info(f"  - Docker image: {image_uri}")
        logger.info(f"  - Full patch:   {full_patch_file.name}")
        logger.info(f"  - Test patch:   {test_patch_file.name}")
        logger.info(f"  - Code patch:   {code_patch_file.name}")
        logger.info(f"  - Metadata:     instance.json")
        logger.info(f"  - 29-Fields:    task_instances_29fields.csv")
        logger.info(f"  - FAIL_TO_PASS: {len(fail_to_pass)} tests (validated: FAIL@Run2, PASS@Run3)")
        logger.info(f"  - PASS_TO_PASS: {len(pass_to_pass)} tests (PASS@Run1 AND PASS@Run3)")
        logger.info("=" * 80)

        # ========== Cleanup ==========
        try:
            cleanup_workspace(
                workspace_root=workspace_path,  # Path, so cleanup_pycache gets Path
                logger=logger,
                keep_repo=keep_repo,
                cleanup_images=cleanup_images,
                docker_image=state.get('docker_image')
            )
        except Exception as cleanup_err:
            logger.exception(f"Cleanup failed (outputs were saved): {cleanup_err}")
            logger.info("Metadata and artifacts are available; repo/disk cleanup did not complete.")

        return 0

    except Exception as e:
        logger.exception(f"Part 2 failed with exception: {e}")
        return 1


def main():
    """Main entry point for Part 2."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Part 2: Patch Application and Evaluation'
    )
    parser.add_argument(
        'workspace',
        help='Workspace root directory'
    )
    parser.add_argument(
        '--keep-repo',
        action='store_true',
        help='Keep cloned repository after completion (default: cleanup)'
    )
    parser.add_argument(
        '--cleanup-images',
        action='store_true',
        help='Remove Docker images after completion (default: keep)'
    )
    
    args = parser.parse_args()

    exit_code = run_part2(
        workspace_root=args.workspace,
        keep_repo=args.keep_repo,
        cleanup_images=args.cleanup_images
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
