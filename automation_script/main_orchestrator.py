#!/usr/bin/env python3
"""
Main Orchestrator for Two-Part PR Evaluation System

This script provides a unified entry point that can:
1. Run both parts sequentially (default)
2. Run only Part 1 (--part1-only)
3. Run only Part 2 (--part2-only)

Usage:
  # Run both parts sequentially
  python -m automation_script.main_orchestrator \\
    https://github.com/owner/repo/pull/NUMBER \\
    /path/to/workspace

  # Override auto-detected settings (useful when auto-detection fails)
  python -m automation_script.main_orchestrator \\
    --language rust \\
    --test-cmd "cargo test --manifest-path engine/Cargo.toml" \\
    https://github.com/owner/repo/pull/123 \\
    /path/to/workspace

  # Use shallow clone for faster processing
  python -m automation_script.main_orchestrator \\
    --shallow-clone \\
    https://github.com/owner/repo/pull/123 \\
    /path/to/workspace

  # Reuse existing Docker image (skip rebuild)
  python -m automation_script.main_orchestrator \\
    --reuse-image pr-eval:base-abc123 \\
    https://github.com/owner/repo/pull/123 \\
    /path/to/workspace

  # Run only Part 2 (requires Part 1 to have run first)
  python -m automation_script.main_orchestrator \\
    --part2-only /path/to/workspace
"""

import sys
import argparse
import logging
from pathlib import Path

from .part1_build_and_base import run_part1
from .part2_patch_and_evaluate import run_part2
from .config import LOGS_DIR, UNIFIED_LOG_FILE


def setup_logging(workspace_root: Path = None) -> logging.Logger:
    """
    Set up unified workflow logging.
    
    Args:
        workspace_root: Path to workspace root. If provided, logs to file + console.
                       If None, logs to console only.
    
    Returns:
        Logger configured for the entire workflow.
    """
    logger = logging.getLogger("workflow")
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
    
    # File handler (DEBUG level) - if workspace provided
    if workspace_root:
        logs_dir = Path(workspace_root) / LOGS_DIR
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / UNIFIED_LOG_FILE
        
        fh = logging.FileHandler(log_file, mode='a')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(log_format)
        logger.addHandler(fh)
        
        # Also configure child loggers to use this logger
        logging.getLogger("part1").parent = logger
        logging.getLogger("part2").parent = logger
        logging.getLogger("orchestrator").parent = logger

    return logger


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Two-Part PR Evaluation System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run both parts sequentially
  %(prog)s https://github.com/owner/repo/pull/123 /path/to/workspace

  # Override language detection (useful for polyglot repos)
  %(prog)s --language rust https://github.com/owner/repo/pull/123 ./workspaces

  # Override test command (when auto-detection fails)
  %(prog)s --test-cmd "cargo test --manifest-path engine/Cargo.toml" <PR_URL> ./workspaces

  # Fast mode: shallow clone + skip tests (just build artifacts)
  %(prog)s --shallow-clone --skip-tests <PR_URL> ./workspaces

  # Reuse existing Docker image (much faster for repeated runs)
  %(prog)s --reuse-image pr-eval:base-abc123 <PR_URL> ./workspaces

  # Run only Part 2 (patch + evaluate) - requires Part 1 to have run first
  %(prog)s --part2-only /path/to/workspace

Part 1: Docker Image Creation + BASE Testing (Run 1)
  - Clone repository
  - Fetch PR refs
  - Determine BASE commit
  - Build immutable Docker image
  - Run 1: BASE tests (no patches applied)
  - Save state for Part 2

Part 2: Patch Application + Evaluation (THREE-RUN SEQUENCE)
  - Generate patch files (full, test, code)
  - Verify patches apply cleanly
  - Run 2: TEST_PATCH only (new tests should FAIL)
  - Run 3: FULL PATCH (new tests should PASS)
  - Categorize tests using proper validation:
    - F2P = FAIL in Run 2 AND PASS in Run 3
    - P2P = PASS in Run 1 AND PASS in Run 3 (excluding F2P)
  - Generate metadata
  - Validate outputs

Supported Languages:
  python, javascript, typescript, go, rust, java, csharp, ruby

Troubleshooting:
  If auto-detection fails, use --language and --test-cmd to override.
  If cloning is slow, use --shallow-clone.
  If Docker build fails, check the error in logs/ and try --reuse-image.
        """
    )

    # Execution mode options
    mode_group = parser.add_argument_group('Execution Mode')
    mode_group.add_argument(
        '--part1-only',
        action='store_true',
        help='Run only Part 1 (build image + BASE tests)'
    )
    mode_group.add_argument(
        '--part2-only',
        action='store_true',
        help='Run only Part 2 (patch + evaluate) - requires Part 1 state'
    )

    # Override options (CRITICAL for avoiding per-PR changes)
    override_group = parser.add_argument_group('Detection Overrides', 
        'Use these when auto-detection fails (avoids editing code for each PR)')
    override_group.add_argument(
        '--language', '-l',
        type=str,
        choices=['python', 'javascript', 'typescript', 'go', 'rust', 'java', 'csharp', 'ruby'],
        help='Override detected language (e.g., --language rust)'
    )
    override_group.add_argument(
        '--test-cmd', '-t',
        type=str,
        dest='test_command',
        help='Override detected test command (e.g., --test-cmd "cargo test --manifest-path subdir/Cargo.toml")'
    )
    override_group.add_argument(
        '--rust-subdir',
        type=str,
        help='For Rust projects: specify subdirectory containing Cargo.toml (e.g., --rust-subdir baml_language)'
    )
    override_group.add_argument(
        '--base-commit',
        type=str,
        metavar='SHA',
        help='Override auto-detected base commit (useful for merged PRs where detection fails)'
    )

    # Performance options
    perf_group = parser.add_argument_group('Performance Options')
    perf_group.add_argument(
        '--shallow-clone',
        action='store_true',
        help='Use shallow clone (faster, then deepen for merge-base)'
    )
    perf_group.add_argument(
        '--reuse-image',
        type=str,
        metavar='IMAGE_TAG',
        help='Reuse existing Docker image instead of rebuilding (e.g., pr-eval:base-abc123)'
    )
    perf_group.add_argument(
        '--skip-tests',
        action='store_true',
        help='Skip running tests (only build Docker image and generate artifacts)'
    )

    # Cleanup options
    cleanup_group = parser.add_argument_group('Cleanup Options')
    cleanup_group.add_argument(
        '--keep-repo',
        action='store_true',
        help='Keep cloned repository after completion (default: cleanup)'
    )
    cleanup_group.add_argument(
        '--cleanup-images',
        action='store_true',
        help='Remove Docker images after completion (default: keep)'
    )

    # Positional arguments
    parser.add_argument(
        'pr_url',
        nargs='?',
        help='GitHub PR URL (required for Part 1)'
    )
    parser.add_argument(
        'workspace',
        help='Workspace root directory'
    )

    args = parser.parse_args()

    # Validation
    if args.part1_only and args.part2_only:
        parser.error("Cannot specify both --part1-only and --part2-only")

    if args.part2_only:
        # Part 2 only needs workspace
        if args.pr_url:
            parser.error("--part2-only does not take a PR URL (loads from state)")
    else:
        # Part 1 or both parts need PR URL
        if not args.pr_url:
            parser.error("PR URL is required unless using --part2-only")

    # If rust-subdir is provided, ensure language is set to rust
    if args.rust_subdir and not args.language:
        args.language = 'rust'

    return args


def main():
    """Main orchestrator entry point."""
    args = parse_arguments()
    
    # Set up unified logging with file handler once we know the workspace
    workspace_path = Path(args.workspace).resolve()
    logger = setup_logging(workspace_path)

    logger.info("=" * 80)
    logger.info("PR EVALUATION SYSTEM - MAIN ORCHESTRATOR")
    logger.info("=" * 80)
    logger.info(f"Unified log file: {workspace_path / LOGS_DIR / UNIFIED_LOG_FILE}")

    # Build overrides dictionary from command-line arguments
    overrides = {}
    if args.language:
        overrides['language'] = args.language
        logger.info(f"Override: language = {args.language}")
    if args.test_command:
        overrides['test_command'] = args.test_command
        logger.info(f"Override: test_command = {args.test_command}")
    if args.rust_subdir:
        overrides['rust_subdir'] = args.rust_subdir
        logger.info(f"Override: rust_subdir = {args.rust_subdir}")
    if args.shallow_clone:
        overrides['shallow_clone'] = True
        logger.info("Override: using shallow clone (faster)")
    if args.reuse_image:
        overrides['reuse_image'] = args.reuse_image
        logger.info(f"Override: reusing existing image = {args.reuse_image}")
    if args.skip_tests:
        overrides['skip_tests'] = True
        logger.info("Override: skipping test execution")
    if args.base_commit:
        overrides['base_commit'] = args.base_commit
        logger.info(f"Override: base_commit = {args.base_commit[:12]}")

    # Determine execution mode
    if args.part2_only:
        logger.info("Mode: Part 2 Only (Patch + Evaluate)")
        logger.info(f"Workspace: {args.workspace}")
        logger.info(f"Keep repo: {args.keep_repo}")
        logger.info(f"Cleanup images: {args.cleanup_images}")
        logger.info("=" * 80)
        logger.info("")

        exit_code = run_part2(args.workspace, args.keep_repo, args.cleanup_images, logger=logger)

        if exit_code == 0:
            logger.info("")
            logger.info("=" * 80)
            logger.info("✓ Part 2 completed successfully")
            logger.info("=" * 80)
        else:
            logger.error("")
            logger.error("=" * 80)
            logger.error("✗ Part 2 failed")
            logger.error("=" * 80)

        sys.exit(exit_code)

    elif args.part1_only:
        logger.info("Mode: Part 1 Only (Build + BASE Tests)")
        logger.info(f"PR URL: {args.pr_url}")
        logger.info(f"Workspace: {args.workspace}")
        if overrides:
            logger.info(f"Overrides: {list(overrides.keys())}")
        logger.info("=" * 80)
        logger.info("")

        exit_code, pr_workspace = run_part1(args.pr_url, args.workspace, overrides=overrides, logger=logger)

        if exit_code == 0:
            logger.info("")
            logger.info("=" * 80)
            logger.info("✓ Part 1 completed successfully")
            logger.info("")
            logger.info("To run Part 2:")
            logger.info(f"  python -m automation_script.main_orchestrator --part2-only {pr_workspace}")
            logger.info("=" * 80)
        else:
            logger.error("")
            logger.error("=" * 80)
            logger.error("✗ Part 1 failed")
            logger.error("=" * 80)
            _print_troubleshooting_help(logger, args, overrides)

        sys.exit(exit_code)

    else:
        # Run both parts sequentially
        logger.info("Mode: Full Workflow (Part 1 + Part 2)")
        logger.info(f"PR URL: {args.pr_url}")
        logger.info(f"Workspace: {args.workspace}")
        logger.info(f"Keep repo: {args.keep_repo}")
        logger.info(f"Cleanup images: {args.cleanup_images}")
        if overrides:
            logger.info(f"Overrides: {list(overrides.keys())}")
        logger.info("=" * 80)
        logger.info("")

        # Part 1
        logger.info("Starting Part 1...")
        logger.info("")
        exit_code, pr_workspace = run_part1(args.pr_url, args.workspace, overrides=overrides, logger=logger)

        if exit_code != 0:
            logger.error("")
            logger.error("=" * 80)
            logger.error("✗ Part 1 failed - stopping workflow")
            logger.error("=" * 80)
            _print_troubleshooting_help(logger, args, overrides)
            sys.exit(exit_code)

        logger.info("")
        logger.info("=" * 80)
        logger.info("✓ Part 1 completed - starting Part 2...")
        logger.info("=" * 80)
        logger.info("")

        # Part 2 - use the PR-specific workspace returned by Part 1
        exit_code = run_part2(pr_workspace, args.keep_repo, args.cleanup_images, logger=logger)

        if exit_code == 0:
            logger.info("")
            logger.info("=" * 80)
            logger.info("✓ FULL WORKFLOW COMPLETED SUCCESSFULLY")
            logger.info("=" * 80)
        else:
            logger.error("")
            logger.error("=" * 80)
            logger.error("✗ Part 2 failed")
            logger.error("=" * 80)

        sys.exit(exit_code)


def _print_troubleshooting_help(logger: logging.Logger, args, overrides: dict):
    """Print helpful troubleshooting suggestions when the script fails."""
    logger.info("")
    logger.info("=" * 80)
    logger.info("TROUBLESHOOTING SUGGESTIONS")
    logger.info("=" * 80)
    
    if 'language' not in overrides:
        logger.info("")
        logger.info("1. If language detection failed, try specifying it explicitly:")
        logger.info(f"   python -m automation_script.main_orchestrator \\")
        logger.info(f"     --language <python|rust|go|java|javascript|typescript|csharp> \\")
        logger.info(f"     {args.pr_url} {args.workspace}")
    
    if 'test_command' not in overrides:
        logger.info("")
        logger.info("2. If test command detection failed, specify it explicitly:")
        logger.info(f"   python -m automation_script.main_orchestrator \\")
        logger.info(f"     --test-cmd 'pytest' \\  # or 'cargo test', 'go test ./...', etc.")
        logger.info(f"     {args.pr_url} {args.workspace}")
    
    logger.info("")
    logger.info("3. For Rust projects with subdirectory layout:")
    logger.info(f"   python -m automation_script.main_orchestrator \\")
    logger.info(f"     --rust-subdir <subdir_containing_Cargo.toml> \\")
    logger.info(f"     {args.pr_url} {args.workspace}")
    
    logger.info("")
    logger.info("4. If Docker build is failing, check logs at:")
    logger.info(f"   {args.workspace}/<pr_folder>/logs/part1_build_and_base.log")
    logger.info(f"   {args.workspace}/<pr_folder>/repo/docker_build_error.log")
    
    logger.info("")
    logger.info("5. For faster runs on large repos, use shallow clone:")
    logger.info(f"   python -m automation_script.main_orchestrator --shallow-clone \\")
    logger.info(f"     {args.pr_url} {args.workspace}")
    
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
