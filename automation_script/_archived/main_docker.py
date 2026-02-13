#!/usr/bin/env python3
"""
Docker-based PR Workflow Automation - Main Entry Point

This is the production-ready system that runs ALL tests inside Docker containers.

CRITICAL CONSTRAINTS:
- NO host virtualenvs
- NO dependency installs on host
- BASE Docker image built ONCE at BASE commit
- PR evaluation uses patch application INSIDE container
- Never checkout PR commit - always apply patch to BASE

WORKFLOW:
1. Parse PR URL
2. Clone repo
3. Compute BASE commit via git merge-base
4. Checkout BASE commit
5. Build Docker image (IMMUTABLE)
6. Save & compress image
7. Generate patch file
8. Run BASE tests in container
9. Run PATCHED tests in container (apply patch inside)
10. Categorize tests (FAIL_TO_PASS, PASS_TO_PASS)
11. Generate metadata
12. Validate outputs
"""

import argparse
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
    generate_patch_file,
    get_patches,
)
from .environment import detect_language, detect_test_command
from .docker_builder_new import (
    check_docker_available,
    build_docker_image,
)
from .docker_runner import (
    compress_docker_image,
    verify_patch_applies,
    run_base_tests,
    run_patched_tests,
)
from .test_runner import categorize_tests
from .metadata import generate_metadata, validate_metadata
from .artifacts import validate_artifacts


def run_workflow(
    pr_url: str,
    workspace_root: Path,
    keep_workspace: bool = False
) -> int:
    """
    Execute the Docker-based PR workflow.

    Args:
        pr_url: Pull request URL
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

    # Additional directories
    patches_dir = workspace.root / "patches"
    patches_dir.mkdir(exist_ok=True)

    log_file = workspace.logs / "workflow.log"
    logger = setup_logging(log_file)

    logger.info("=" * 80)
    logger.info("DOCKER-BASED PR WORKFLOW AUTOMATION")
    logger.info("=" * 80)
    logger.info(f"PR URL:      {pr_url}")
    logger.info(f"Workspace:   {workspace.root}")
    logger.info(f"Keep files:  {keep_workspace}")
    logger.info("=" * 80)

    try:
        # =====================================================================
        # STEP 1: CHECK DOCKER
        # =====================================================================
        logger.info("\n[STEP 1] Checking Docker availability")
        logger.info("-" * 40)

        if not check_docker_available(logger):
            logger.error("FAILED: Docker not available")
            return 1

        # =====================================================================
        # STEP 2: PARSE PR URL
        # =====================================================================
        logger.info("\n[STEP 2] Parsing PR URL")
        logger.info("-" * 40)

        pr_info = parse_pr_url(pr_url, logger)
        logger.info(f"Repository: {pr_info.owner}/{pr_info.repo}")
        logger.info(f"PR Number:  {pr_info.pr_number}")

        # =====================================================================
        # STEP 3: CLONE REPOSITORY
        # =====================================================================
        logger.info("\n[STEP 3] Cloning repository")
        logger.info("-" * 40)

        if not clone_repo(pr_info.clone_url, workspace.repo, logger):
            logger.error("FAILED: Could not clone repository")
            return 1

        # =====================================================================
        # STEP 4: FETCH PR REFS & DETERMINE COMMITS
        # =====================================================================
        logger.info("\n[STEP 4] Fetching PR refs and determining commits")
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
        logger.info(f"BASE commit:   {base_commit[:12]}")
        logger.info(f"PR commit:     {pr_sha[:12]}")

        # =====================================================================
        # STEP 5: CHECKOUT BASE COMMIT
        # =====================================================================
        logger.info("\n[STEP 5] Checking out BASE commit")
        logger.info("-" * 40)

        if not checkout_commit(workspace.repo, base_commit, logger):
            logger.error("FAILED: Could not checkout BASE commit")
            return 1

        # =====================================================================
        # STEP 6: DETECT LANGUAGE & TEST COMMAND
        # =====================================================================
        logger.info("\n[STEP 6] Detecting language and test command")
        logger.info("-" * 40)

        language = detect_language(workspace.repo, logger)
        test_command = detect_test_command(workspace.repo, language, logger)

        logger.info(f"Language:     {language}")
        logger.info(f"Test command: {test_command}")

        # =====================================================================
        # STEP 7: BUILD DOCKER IMAGE AT BASE
        # =====================================================================
        logger.info("\n[STEP 7] Building Docker image at BASE commit")
        logger.info("-" * 40)
        logger.info("⚠️  This image is IMMUTABLE and will be used for both BASE and PR tests")

        image_tag = build_docker_image(
            workspace.repo,
            base_commit,
            language,
            logger
        )

        if not image_tag:
            logger.error("FAILED: Could not build Docker image")
            return 1

        # =====================================================================
        # STEP 8: SAVE & COMPRESS DOCKER IMAGE
        # =====================================================================
        logger.info("\n[STEP 8] Saving and compressing Docker image")
        logger.info("-" * 40)

        image_tar_gz = workspace.docker_images / f"base-{base_commit[:12]}.tar.gz"
        if not compress_docker_image(image_tag, image_tar_gz, logger):
            logger.error("FAILED: Could not compress Docker image")
            return 1

        image_uri = f"file://{image_tar_gz.absolute()}"
        logger.info(f"Image URI: {image_uri}")

        # =====================================================================
        # STEP 9: GENERATE PATCH FILE
        # =====================================================================
        logger.info("\n[STEP 9] Generating patch file")
        logger.info("-" * 40)

        patch_file = patches_dir / "pr.patch"
        if not generate_patch_file(workspace.repo, base_commit, pr_sha, patch_file, logger):
            logger.error("FAILED: Could not generate patch")
            return 1

        # =====================================================================
        # STEP 10: VERIFY PATCH APPLIES
        # =====================================================================
        logger.info("\n[STEP 10] Verifying patch applies cleanly")
        logger.info("-" * 40)

        applies, error_msg = verify_patch_applies(
            image_tag,
            workspace.root,
            workspace.repo,
            patch_file,
            logger
        )

        if not applies:
            logger.error(f"FAILED: {error_msg}")
            logger.error("Patch does not apply cleanly - aborting workflow")
            return 1

        logger.info("✓ Patch verification successful")

        # =====================================================================
        # STEP 11: RUN BASE TESTS IN CONTAINER
        # =====================================================================
        logger.info("\n[STEP 11] Running BASE tests in container")
        logger.info("-" * 40)
        logger.info("Container: ephemeral, spawned from BASE image")

        base_result = run_base_tests(
            image_tag,
            workspace.root,
            workspace.repo,
            test_command,
            logger
        )

        if not base_result:
            logger.error("FAILED: BASE tests did not produce results")
            return 1

        logger.info(f"BASE results: {len(base_result.tests_passed)} passed, "
                   f"{len(base_result.tests_failed)} failed")

        # =====================================================================
        # STEP 12: RUN PATCHED TESTS IN CONTAINER
        # =====================================================================
        logger.info("\n[STEP 12] Running PATCHED tests in container")
        logger.info("-" * 40)
        logger.info("Container: ephemeral, spawned from BASE image")
        logger.info("Patch: applied INSIDE container")

        pr_result = run_patched_tests(
            image_tag,
            workspace.root,
            workspace.repo,
            patch_file,
            test_command,
            logger
        )

        if not pr_result:
            logger.error("FAILED: PATCHED tests did not produce results")
            return 1

        logger.info(f"PATCHED results: {len(pr_result.tests_passed)} passed, "
                   f"{len(pr_result.tests_failed)} failed")

        # =====================================================================
        # STEP 13: CATEGORIZE TESTS
        # =====================================================================
        logger.info("\n[STEP 13] Categorizing tests")
        logger.info("-" * 40)

        fail_to_pass, pass_to_pass = categorize_tests(base_result, pr_result, logger)

        logger.info(f"FAIL_TO_PASS: {len(fail_to_pass)} tests")
        logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests")

        if not fail_to_pass:
            logger.warning("⚠️  No FAIL_TO_PASS tests found")

        # =====================================================================
        # STEP 14: GENERATE METADATA
        # =====================================================================
        logger.info("\n[STEP 14] Generating metadata")
        logger.info("-" * 40)

        # Get patches for metadata
        patch_str, test_patch_str = get_patches(
            workspace.repo,
            base_commit,
            pr_sha,
            logger
        )

        metadata = generate_metadata(
            pr_info=pr_info,
            base_commit=base_commit,
            pr_commit=pr_sha,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            language=language,
            test_command=test_command,
            image_uri=image_uri,
            patch=patch_str,
            test_patch=test_patch_str,
            metadata_path=workspace.metadata,
            logger=logger
        )

        # =====================================================================
        # STEP 15: VALIDATE OUTPUTS
        # =====================================================================
        logger.info("\n[STEP 15] Validating outputs")
        logger.info("-" * 40)

        base_valid, _ = validate_artifacts(workspace.artifacts_base, "result", logger)
        pr_valid, _ = validate_artifacts(workspace.artifacts_pr, "result", logger)
        metadata_valid = validate_metadata(workspace.metadata, logger)

        # Check Docker image exists
        if not image_tar_gz.exists():
            logger.error(f"Docker image tarball missing: {image_tar_gz}")
            metadata_valid = False

        # Check patch exists
        if not patch_file.exists():
            logger.error(f"Patch file missing: {patch_file}")
            metadata_valid = False

        if not all([base_valid, pr_valid, metadata_valid]):
            logger.warning("⚠️  Some validation checks failed")

        # =====================================================================
        # STEP 16: CLEANUP
        # =====================================================================
        logger.info("\n[STEP 16] Cleanup")
        logger.info("-" * 40)

        if not keep_workspace:
            logger.info("Cleaning up repository (keeping artifacts)")
            import shutil
            if workspace.repo.exists():
                shutil.rmtree(workspace.repo, ignore_errors=True)
        else:
            logger.info("Keeping workspace (--keep-workspace)")

        # =====================================================================
        # DONE
        # =====================================================================
        logger.info("\n" + "=" * 80)
        logger.info("WORKFLOW COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        logger.info(f"Metadata:       {workspace.metadata / 'instance.json'}")
        logger.info(f"BASE artifacts: {workspace.artifacts_base}")
        logger.info(f"PR artifacts:   {workspace.artifacts_pr}")
        logger.info(f"Docker image:   {image_tar_gz}")
        logger.info(f"Patch file:     {patch_file}")
        logger.info(f"Log file:       {log_file}")
        logger.info("=" * 80)
        logger.info("\n📦 BENCHMARK ARTIFACTS:")
        logger.info(f"  - Docker image: {image_tar_gz.name} ({image_tar_gz.stat().st_size / (1024**2):.1f} MB)")
        logger.info(f"  - Patch:        {patch_file.name} ({patch_file.stat().st_size / 1024:.1f} KB)")
        logger.info(f"  - Metadata:     instance.json")
        logger.info("=" * 80)

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
        description="Docker-based PR Workflow Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s https://github.com/owner/repo/pull/123
  %(prog)s https://github.com/owner/repo/pull/123 --workspace ./output --keep-workspace

Design:
  - BASE Docker image built ONCE at BASE commit
  - Image saved as compressed tar.gz (immutable artifact)
  - PR evaluated by applying patch INSIDE container
  - No host virtualenvs or dependency installs
  - Two ephemeral containers spawned from same BASE image

Output:
  workspace/
  ├── docker_images/base-<sha>.tar.gz  # Compressed Docker image
  ├── patches/pr.patch                 # Git diff
  ├── metadata/instance.json           # 13-field metadata
  ├── artifacts/
  │   ├── base/result.json             # BASE test results
  │   └── pr/result.json               # PATCHED test results
  └── logs/workflow.log
        """
    )

    parser.add_argument(
        "pr_url",
        help="Pull Request URL (GitHub, GitLab, or Bitbucket)"
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
        help="Keep workspace files after completion"
    )

    args = parser.parse_args()

    exit_code = run_workflow(
        pr_url=args.pr_url,
        workspace_root=Path(args.workspace),
        keep_workspace=args.keep_workspace,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
