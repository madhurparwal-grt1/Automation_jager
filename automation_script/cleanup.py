#!/usr/bin/env python3
"""
Cleanup utilities for removing temporary files and directories after workflow completion.
"""

import os
import stat
import shutil
import subprocess
import logging
import pwd
from pathlib import Path
from typing import Optional


def remove_readonly(func, path, excinfo):
    """Error handler for shutil.rmtree to handle read-only files."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_rmtree(path: Path, logger: logging.Logger) -> bool:
    """
    Safely remove a directory tree, handling permission errors.
    
    Args:
        path: Path to directory to remove
        logger: Logger instance
        
    Returns:
        True if successful, False otherwise
    """
    if not path.exists():
        logger.debug(f"Path does not exist, skipping: {path}")
        return True
        
    try:
        logger.info(f"Removing directory: {path}")
        shutil.rmtree(path, onerror=remove_readonly)
        logger.info(f"Successfully removed: {path}")
        return True
        
    except PermissionError as e:
        logger.warning(f"Permission error when removing {path}: {e}")
        logger.info(f"Attempting to fix permissions and retry...")
        
        try:
            # Get current username
            username = pwd.getpwuid(os.getuid()).pw_name
            
            # Try to change ownership to current user
            result = subprocess.run(
                ["sudo", "chown", "-R", f"{username}:{username}", str(path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Try again after fixing permissions
                shutil.rmtree(path, onerror=remove_readonly)
                logger.info(f"Successfully removed {path} after fixing permissions")
                return True
            else:
                # Fall back to sudo rm
                logger.info(f"Falling back to sudo rm...")
                result = subprocess.run(
                    ["sudo", "rm", "-rf", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0:
                    logger.info(f"Successfully removed {path} with sudo")
                    return True
                else:
                    logger.error(f"Failed to remove {path}: {result.stderr}")
                    return False
                    
        except Exception as ex:
            logger.error(f"Failed to remove directory with elevated permissions: {ex}")
            return False
            
    except Exception as e:
        logger.error(f"Unexpected error removing {path}: {e}")
        return False


def cleanup_repo(repo_path: Path, logger: logging.Logger) -> bool:
    """
    Remove cloned repository directory to free up disk space.
    
    Args:
        repo_path: Path to repository directory
        logger: Logger instance
        
    Returns:
        True if successful, False otherwise
    """
    logger.info("=" * 80)
    logger.info("CLEANING UP CLONED REPOSITORY")
    logger.info("=" * 80)
    
    if not repo_path.exists():
        logger.info(f"Repository path does not exist: {repo_path}")
        return True
    
    # Get size before deletion for logging
    try:
        result = subprocess.run(
            ["du", "-sh", str(repo_path)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            size = result.stdout.split()[0]
            logger.info(f"Repository size: {size}")
    except:
        pass
    
    # Remove the repository
    success = safe_rmtree(repo_path, logger)
    
    if success:
        logger.info("✓ Repository cleanup complete")
        logger.info("  Artifacts, metadata, patches, and logs are preserved")
    else:
        logger.warning("⚠️  Repository cleanup incomplete - manual cleanup may be needed")
    
    logger.info("=" * 80)
    logger.info("")
    
    return success


def cleanup_pycache(workspace_root, logger: logging.Logger) -> None:
    """
    Remove __pycache__ directories from workspace.

    Args:
        workspace_root: Root workspace directory (Path or str)
        logger: Logger instance
    """
    logger.info("Cleaning up __pycache__ directories...")
    root = Path(workspace_root)
    pycache_count = 0
    for pycache_dir in root.rglob("__pycache__"):
        try:
            shutil.rmtree(pycache_dir, ignore_errors=True)
            pycache_count += 1
        except:
            pass
    
    if pycache_count > 0:
        logger.info(f"Removed {pycache_count} __pycache__ directories")
    else:
        logger.debug("No __pycache__ directories found")


def cleanup_docker_image(image_tag: str, logger: logging.Logger) -> bool:
    """
    Remove Docker image to free up disk space.
    
    Args:
        image_tag: Docker image tag to remove
        logger: Logger instance
        
    Returns:
        True if successful, False otherwise
    """
    logger.info(f"Removing Docker image: {image_tag}")
    
    try:
        result = subprocess.run(
            ["docker", "rmi", image_tag],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            logger.info(f"✓ Docker image removed: {image_tag}")
            return True
        else:
            logger.warning(f"Failed to remove Docker image: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Error removing Docker image: {e}")
        return False


def cleanup_workspace(
    workspace_root: Path,
    logger: logging.Logger,
    keep_repo: bool = False,
    cleanup_images: bool = False,
    docker_image: Optional[str] = None
) -> None:
    """
    Perform cleanup of workspace after workflow completion.
    
    Args:
        workspace_root: Root workspace directory
        logger: Logger instance
        keep_repo: If True, keep the cloned repository (default: False)
        cleanup_images: If True, remove Docker images (default: False)
        docker_image: Docker image tag to remove (if cleanup_images=True)
    """
    logger.info("")
    logger.info("=" * 80)
    logger.info("WORKSPACE CLEANUP")
    logger.info("=" * 80)
    root = Path(workspace_root) if not isinstance(workspace_root, Path) else workspace_root

    # Always clean up __pycache__
    cleanup_pycache(root, logger)

    # Clean up repository unless --keep-repo is specified
    if not keep_repo:
        repo_path = root / "repo"
        cleanup_repo(repo_path, logger)
    else:
        logger.info("Keeping repository as requested (--keep-repo)")
    
    # Optionally clean up Docker images
    if cleanup_images and docker_image:
        cleanup_docker_image(docker_image, logger)
    
    logger.info("Cleanup complete!")
    logger.info("")
