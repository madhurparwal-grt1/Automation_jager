#!/usr/bin/env python3
"""
PR Workflow Automation - Main Entry Point

This script automates the complete workflow for analyzing a Pull Request:

WORKFLOW OVERVIEW:
==================

1. PARSE PR URL
   - Extract owner, repo, PR number from GitHub/GitLab/Bitbucket URLs

2. CLONE & SETUP
   - Clone repository
   - Fetch PR refs
   - Determine base commit via git merge-base

3. BASE COMMIT PHASE
   ┌─────────────────────────────────────────────────────────┐
   │  a) Checkout BASE commit (where PR branch diverged)    │
   │  b) Setup virtual environment                          │
   │  c) Run tests with retries + environment healing       │
   │  d) Record which tests PASS and which FAIL             │
   │  e) Build Docker image at this state                   │
   │  f) Save artifacts to artifacts/base/                  │
   └─────────────────────────────────────────────────────────┘

4. PR COMMIT PHASE
   ┌─────────────────────────────────────────────────────────┐
   │  a) Checkout PR commit (the changes to evaluate)       │
   │  b) Reinstall dependencies (may have changed)          │
   │  c) Run tests with retries + environment healing       │
   │  d) Record which tests PASS and which FAIL             │
   │  e) Save artifacts to artifacts/pr/                    │
   └─────────────────────────────────────────────────────────┘

5. CATEGORIZE TESTS
   - FAIL_TO_PASS: Tests that FAILED on base but PASS on PR
     (These are the tests the PR is fixing)
   - PASS_TO_PASS: Tests that PASSED on both base and PR
     (These ensure no regressions)

6. GENERATE METADATA
   - Create instance.json with all required fields
   - Generate git patches

7. CLEANUP
   - Remove repo unless --keep-workspace

Usage:
    python -m automation_script.main <PR_URL> [options]

    python -m automation_script.main https://github.com/owner/repo/pull/123
    python -m automation_script.main https://github.com/owner/repo/pull/123 --max-retries 5
    python -m automation_script.main https://github.com/owner/repo/pull/123 --workspace ./my_workspace --keep-workspace
"""

import argparse
import shutil
import sys
from pathlib import Path

from .config import WorkspaceConfig, DEFAULT_MAX_RETRIES
from .utils import setup_logging
from .git_operations import (
    parse_pr_url,
    clone_repo,
    fetch_pr_refs,
    detect_target_branch,
    get_base_commit,
    checkout_commit,
    get_patches,
)
from .environment import (
    detect_language,
    detect_test_command,
    setup_environment,
    heal_environment,
)
from .test_runner import (
    run_tests,
    categorize_tests,
    is_environment_error,
)
from .docker_builder import build_docker_image
from .artifacts import save_test_artifacts, validate_artifacts
from .metadata import generate_metadata, validate_metadata


def cleanup_workspace(workspace: WorkspaceConfig, keep: bool, logger) -> None:
    """
    Clean up the workspace after workflow completion.

    Args:
        workspace: Workspace configuration
        keep: Whether to keep the workspace
        logger: Logger instance
    """
    if keep:
        logger.info("Keeping workspace as requested (--keep-workspace)")
        return

    logger.info("Cleaning up workspace...")

    # Remove repo directory
    if workspace.repo.exists():
        # First remove venv (can have permission issues on some systems)
        venv_path = workspace.repo / ".venv"
        if venv_path.exists():
            shutil.rmtree(venv_path, ignore_errors=True)

        shutil.rmtree(workspace.repo, ignore_errors=True)
        logger.info("Repository removed")

    logger.info("Cleanup complete")


def run_workflow(
    pr_url: str,
    max_retries: int,
    workspace_root: Path,
    keep_workspace: bool
) -> int:
    """
    Execute the complete PR workflow.

    This is the main orchestration function that:
    1. Clones the repo and checks out base commit
    2. Runs tests on base commit
    3. Builds Docker image
    4. Checks out PR commit
    5. Runs tests on PR commit
    6. Categorizes tests and generates metadata

    Args:
        pr_url: Pull request URL
        max_retries: Maximum test retry attempts
        workspace_root: Root directory for workspace
        keep_workspace: Whether to keep workspace after completion

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    # =========================================================================
    # SETUP WORKSPACE
    # =========================================================================
    workspace = WorkspaceConfig.create(workspace_root)
    workspace.create_directories()

    # Setup logging
    log_file = workspace.logs / "workflow.log"
    logger = setup_logging(log_file)

    logger.info("=" * 70)
    logger.info("PR WORKFLOW AUTOMATION")
    logger.info("=" * 70)
    logger.info(f"PR URL:      {pr_url}")
    logger.info(f"Workspace:   {workspace.root}")
    logger.info(f"Max retries: {max_retries}")
    logger.info(f"Keep files:  {keep_workspace}")
    logger.info("=" * 70)

    try:
        # =====================================================================
        # STEP 1: PARSE PR URL
        # =====================================================================
        logger.info("\n[STEP 1] Parsing PR URL")
        logger.info("-" * 40)

        pr_info = parse_pr_url(pr_url, logger)

        # =====================================================================
        # STEP 2: CLONE REPOSITORY
        # =====================================================================
        logger.info("\n[STEP 2] Cloning repository")
        logger.info("-" * 40)

        if not clone_repo(pr_info.clone_url, workspace.repo, logger):
            logger.error("FAILED: Could not clone repository")
            return 1

        # =====================================================================
        # STEP 3: FETCH PR REFS & DETERMINE COMMITS
        # =====================================================================
        logger.info("\n[STEP 3] Fetching PR refs and determining commits")
        logger.info("-" * 40)

        pr_sha = fetch_pr_refs(workspace.repo, pr_info, logger)
        if not pr_sha:
            logger.error("FAILED: Could not fetch PR refs")
            return 1

        target_branch = detect_target_branch(workspace.repo, logger)
        base_commit = get_base_commit(workspace.repo, target_branch, pr_sha, pr_info, logger)
        if not base_commit:
            logger.error("FAILED: Could not determine base commit")
            return 1

        logger.info(f"Target branch: {target_branch}")
        logger.info(f"Base commit:   {base_commit}")
        logger.info(f"PR commit:     {pr_sha}")

        # =====================================================================
        # STEP 4: DETECT LANGUAGE AND TEST COMMAND
        # =====================================================================
        logger.info("\n[STEP 4] Detecting language and test command")
        logger.info("-" * 40)

        language = detect_language(workspace.repo, logger)
        test_command = detect_test_command(workspace.repo, language, logger)

        logger.info(f"Language:     {language}")
        logger.info(f"Test command: {test_command}")

        # =====================================================================
        # STEP 5: BASE COMMIT PHASE
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("BASE COMMIT PHASE")
        logger.info("=" * 70)

        # 5a: Checkout base commit
        logger.info("\n[STEP 5a] Checking out BASE commit")
        logger.info("-" * 40)

        if not checkout_commit(workspace.repo, base_commit, logger):
            logger.error("FAILED: Could not checkout base commit")
            return 1

        # 5b: Setup environment
        logger.info("\n[STEP 5b] Setting up environment for BASE")
        logger.info("-" * 40)

        venv_path = setup_environment(workspace.repo, language, logger)

        # 5c: Run tests with retries and healing
        logger.info("\n[STEP 5c] Running tests on BASE commit")
        logger.info("-" * 40)

        base_result = None
        for attempt in range(max_retries):
            logger.info(f"Test attempt {attempt + 1}/{max_retries}")

            base_result = run_tests(
                workspace.repo,
                language,
                test_command,
                workspace.artifacts_base,
                venv_path,
                logger
            )

            # If tests ran (even with failures), that's OK for base
            # We expect some tests to fail on base (FAIL_TO_PASS)
            if not is_environment_error(base_result):
                logger.info("BASE tests executed successfully")
                break

            # Try to heal environment
            if attempt < max_retries - 1:
                logger.warning("Environment error detected, attempting to heal...")
                heal_environment(
                    workspace.repo,
                    venv_path,
                    base_result,
                    language,
                    attempt,
                    logger
                )

        if base_result is None:
            logger.error("FAILED: No test results from BASE")
            return 1

        # 5d: Save base artifacts
        logger.info("\n[STEP 5d] Saving BASE artifacts")
        logger.info("-" * 40)

        save_test_artifacts(base_result, workspace.artifacts_base, "base", logger)

        # 5e: Build Docker image
        logger.info("\n[STEP 5e] Building Docker image from BASE")
        logger.info("-" * 40)

        image_uri = build_docker_image(
            workspace.repo,
            base_commit,
            workspace.docker_images,
            pr_info,
            language,
            logger
        )

        if not image_uri:
            logger.warning("Docker image build failed (continuing without image)")

        # =====================================================================
        # STEP 6: PR COMMIT PHASE
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("PR COMMIT PHASE")
        logger.info("=" * 70)

        # 6a: Checkout PR commit
        logger.info("\n[STEP 6a] Checking out PR commit")
        logger.info("-" * 40)

        if not checkout_commit(workspace.repo, pr_sha, logger):
            logger.error("FAILED: Could not checkout PR commit")
            return 1

        # 6b: Reinstall dependencies (may have changed in PR)
        logger.info("\n[STEP 6b] Setting up environment for PR")
        logger.info("-" * 40)

        venv_path = setup_environment(workspace.repo, language, logger)

        # 6c: Run tests with retries and healing
        logger.info("\n[STEP 6c] Running tests on PR commit")
        logger.info("-" * 40)

        pr_result = None
        for attempt in range(max_retries):
            logger.info(f"Test attempt {attempt + 1}/{max_retries}")

            pr_result = run_tests(
                workspace.repo,
                language,
                test_command,
                workspace.artifacts_pr,
                venv_path,
                logger
            )

            # For PR, we expect FAIL_TO_PASS tests to now pass
            if not is_environment_error(pr_result):
                logger.info("PR tests executed successfully")
                break

            if attempt < max_retries - 1:
                logger.warning("Environment error detected, attempting to heal...")
                heal_environment(
                    workspace.repo,
                    venv_path,
                    pr_result,
                    language,
                    attempt,
                    logger
                )

        if pr_result is None:
            logger.error("FAILED: No test results from PR")
            return 1

        # 6d: Save PR artifacts
        logger.info("\n[STEP 6d] Saving PR artifacts")
        logger.info("-" * 40)

        save_test_artifacts(pr_result, workspace.artifacts_pr, "pr", logger)

        # =====================================================================
        # STEP 7: CATEGORIZE TESTS
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("TEST CATEGORIZATION")
        logger.info("=" * 70)

        fail_to_pass, pass_to_pass = categorize_tests(base_result, pr_result, logger)

        logger.info(f"\nFAIL_TO_PASS: {len(fail_to_pass)} tests")
        logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests")

        if not fail_to_pass:
            logger.warning("No FAIL_TO_PASS tests found - PR may not be fixing any tests")

        # =====================================================================
        # STEP 8: GENERATE PATCHES
        # =====================================================================
        logger.info("\n[STEP 8] Generating patches")
        logger.info("-" * 40)

        patch, test_patch = get_patches(workspace.repo, base_commit, pr_sha, logger)

        # =====================================================================
        # STEP 9: GENERATE METADATA
        # =====================================================================
        logger.info("\n[STEP 9] Generating metadata")
        logger.info("-" * 40)

        metadata = generate_metadata(
            pr_info=pr_info,
            base_commit=base_commit,
            pr_commit=pr_sha,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            language=language,
            test_command=test_command,
            image_uri=image_uri,
            patch=patch,
            test_patch=test_patch,
            metadata_path=workspace.metadata,
            logger=logger
        )

        # =====================================================================
        # STEP 10: VALIDATE OUTPUTS
        # =====================================================================
        logger.info("\n[STEP 10] Validating outputs")
        logger.info("-" * 40)

        base_valid, base_errors = validate_artifacts(
            workspace.artifacts_base, "base", logger
        )
        pr_valid, pr_errors = validate_artifacts(
            workspace.artifacts_pr, "pr", logger
        )
        metadata_valid = validate_metadata(workspace.metadata, logger)

        if not all([base_valid, pr_valid, metadata_valid]):
            logger.warning("Some validation errors occurred (see above)")

        # =====================================================================
        # STEP 11: CLEANUP
        # =====================================================================
        logger.info("\n[STEP 11] Cleanup")
        logger.info("-" * 40)

        cleanup_workspace(workspace, keep_workspace, logger)

        # =====================================================================
        # DONE
        # =====================================================================
        logger.info("\n" + "=" * 70)
        logger.info("WORKFLOW COMPLETED SUCCESSFULLY")
        logger.info("=" * 70)
        logger.info(f"Metadata:      {workspace.metadata / 'instance.json'}")
        logger.info(f"Base artifacts: {workspace.artifacts_base}")
        logger.info(f"PR artifacts:   {workspace.artifacts_pr}")
        logger.info(f"Docker image:   {image_uri or 'N/A'}")
        logger.info(f"Log file:       {log_file}")
        logger.info("=" * 70)

        return 0

    except KeyboardInterrupt:
        logger.warning("\nWorkflow interrupted by user")
        return 130

    except Exception as e:
        logger.exception(f"Workflow failed with error: {e}")
        return 1


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="PR Workflow Automation - Analyze PRs and generate test metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s https://github.com/owner/repo/pull/123
  %(prog)s https://github.com/owner/repo/pull/123 --max-retries 5
  %(prog)s https://github.com/owner/repo/pull/123 --workspace ./output --keep-workspace

Supported platforms:
  - GitHub:    https://github.com/owner/repo/pull/123
  - GitLab:    https://gitlab.com/owner/repo/-/merge_requests/123
  - Bitbucket: https://bitbucket.org/owner/repo/pull-requests/123

Output structure:
  workspace/
  ├── repo/              # Cloned repository (cleaned unless --keep-workspace)
  ├── artifacts/
  │   ├── base/          # Test results from BASE commit
  │   └── pr/            # Test results from PR commit
  ├── docker_images/     # Docker image tar files
  ├── metadata/          # instance.json with all metadata
  └── logs/              # Workflow logs
        """
    )

    parser.add_argument(
        "pr_url",
        help="Pull Request URL (GitHub, GitLab, or Bitbucket)"
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        metavar="N",
        help=f"Maximum test retry attempts (default: {DEFAULT_MAX_RETRIES})"
    )

    parser.add_argument(
        "--workspace",
        type=str,
        default="./workspace",
        metavar="DIR",
        help="Workspace root directory (default: ./workspace)"
    )

    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Keep workspace files after completion (default: cleanup repo)"
    )

    args = parser.parse_args()

    exit_code = run_workflow(
        pr_url=args.pr_url,
        max_retries=args.max_retries,
        workspace_root=Path(args.workspace),
        keep_workspace=args.keep_workspace,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
