#!/usr/bin/env python3
"""
Standalone script to cleanup old PR workspaces.

Usage:
    # Clean up a specific workspace
    python -m automation_script.cleanup_workspaces /path/to/workspace

    # Clean up all workspaces in a directory
    python -m automation_script.cleanup_workspaces --all /path/to/workspaces/parent

    # Dry run to see what would be deleted
    python -m automation_script.cleanup_workspaces --dry-run /path/to/workspace
"""

import sys
import argparse
import logging
from pathlib import Path
from typing import List

from .cleanup import cleanup_repo, cleanup_pycache, cleanup_docker_image


def setup_logging() -> logging.Logger:
    """Set up logging for cleanup script."""
    logger = logging.getLogger("cleanup")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))

    logger.addHandler(ch)
    return logger


def find_workspaces(parent_dir: Path) -> List[Path]:
    """
    Find all PR workspaces in a parent directory.
    
    Args:
        parent_dir: Parent directory containing PR workspaces
        
    Returns:
        List of workspace paths
    """
    workspaces = []
    
    # Look for directories with state.json (indicator of a PR workspace)
    for child in parent_dir.iterdir():
        if child.is_dir():
            state_file = child / "state.json"
            if state_file.exists():
                workspaces.append(child)
    
    return sorted(workspaces)


def get_workspace_info(workspace: Path, logger: logging.Logger) -> dict:
    """
    Get information about a workspace.
    
    Args:
        workspace: Workspace path
        logger: Logger instance
        
    Returns:
        Dictionary with workspace info
    """
    info = {
        'path': workspace,
        'has_repo': (workspace / 'repo').exists(),
        'has_artifacts': (workspace / 'artifacts').exists(),
        'has_metadata': (workspace / 'metadata').exists(),
    }
    
    # Try to load state.json
    state_file = workspace / 'state.json'
    if state_file.exists():
        try:
            import json
            with open(state_file, 'r') as f:
                state = json.load(f)
                info['pr_number'] = state.get('pr_number')
                info['repo_name'] = state.get('repo')
        except:
            pass
    
    return info


def cleanup_single_workspace(
    workspace: Path,
    logger: logging.Logger,
    dry_run: bool = False,
    keep_artifacts: bool = True
) -> bool:
    """
    Clean up a single workspace.
    
    Args:
        workspace: Workspace path
        logger: Logger instance
        dry_run: If True, only show what would be deleted
        keep_artifacts: If True, keep artifacts/metadata/patches/logs
        
    Returns:
        True if successful, False otherwise
    """
    logger.info("=" * 80)
    logger.info(f"Cleaning workspace: {workspace}")
    logger.info("=" * 80)
    
    # Get workspace info
    info = get_workspace_info(workspace, logger)
    
    if 'repo_name' in info:
        logger.info(f"Repository: {info['repo_name']}")
    if 'pr_number' in info:
        logger.info(f"PR Number: {info['pr_number']}")
    
    logger.info(f"Has repo: {info['has_repo']}")
    logger.info(f"Has artifacts: {info['has_artifacts']}")
    logger.info(f"Has metadata: {info['has_metadata']}")
    logger.info("")
    
    if dry_run:
        logger.info("DRY RUN - No files will be deleted")
        if info['has_repo']:
            logger.info("Would delete: repo/")
        logger.info("Would delete: __pycache__ directories")
        if not keep_artifacts:
            logger.info("Would delete: artifacts/, metadata/, patches/, logs/")
        logger.info("")
        return True
    
    success = True
    
    # Clean up repo
    if info['has_repo']:
        repo_path = workspace / 'repo'
        if not cleanup_repo(repo_path, logger):
            success = False
    
    # Clean up pycache
    cleanup_pycache(workspace, logger)
    
    # Optionally clean up artifacts
    if not keep_artifacts:
        logger.warning("Removing artifacts, metadata, patches, and logs")
        import shutil
        for dirname in ['artifacts', 'metadata', 'patches', 'logs']:
            dir_path = workspace / dirname
            if dir_path.exists():
                logger.info(f"Removing: {dir_path}")
                shutil.rmtree(dir_path, ignore_errors=True)
    
    logger.info("✓ Workspace cleanup complete")
    logger.info("")
    
    return success


def main():
    """Main entry point for cleanup script."""
    parser = argparse.ArgumentParser(
        description='Cleanup old PR workspaces',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Clean up a specific workspace (removes repo/, keeps artifacts)
  %(prog)s /path/to/workspace

  # Clean up all workspaces in a directory
  %(prog)s --all /path/to/workspaces/parent

  # Dry run to see what would be deleted
  %(prog)s --dry-run /path/to/workspace

  # Remove everything including artifacts (use with caution!)
  %(prog)s --remove-artifacts /path/to/workspace
        """
    )
    
    parser.add_argument(
        'path',
        help='Path to workspace or parent directory (with --all)'
    )
    
    parser.add_argument(
        '--all',
        action='store_true',
        help='Clean up all PR workspaces in the specified directory'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without actually deleting'
    )
    
    parser.add_argument(
        '--remove-artifacts',
        action='store_true',
        help='Also remove artifacts/metadata/patches/logs (use with caution!)'
    )
    
    args = parser.parse_args()
    logger = setup_logging()
    
    path = Path(args.path).resolve()
    
    if not path.exists():
        logger.error(f"Path not found: {path}")
        sys.exit(1)
    
    if args.all:
        # Clean up all workspaces in directory
        logger.info("=" * 80)
        logger.info("BATCH WORKSPACE CLEANUP")
        logger.info("=" * 80)
        logger.info(f"Parent directory: {path}")
        logger.info("")
        
        workspaces = find_workspaces(path)
        
        if not workspaces:
            logger.info("No workspaces found")
            sys.exit(0)
        
        logger.info(f"Found {len(workspaces)} workspace(s)")
        logger.info("")
        
        for workspace in workspaces:
            cleanup_single_workspace(
                workspace=workspace,
                logger=logger,
                dry_run=args.dry_run,
                keep_artifacts=not args.remove_artifacts
            )
        
        logger.info("=" * 80)
        logger.info("✓ Batch cleanup complete")
        logger.info("=" * 80)
    else:
        # Clean up single workspace
        if not path.is_dir():
            logger.error(f"Not a directory: {path}")
            sys.exit(1)
        
        success = cleanup_single_workspace(
            workspace=path,
            logger=logger,
            dry_run=args.dry_run,
            keep_artifacts=not args.remove_artifacts
        )
        
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
