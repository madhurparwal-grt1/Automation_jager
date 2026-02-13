"""
Wrapper functions for git_operations to provide simplified interfaces.
"""

import logging
from pathlib import Path
from typing import Optional

from .git_operations import (
    clone_repo,
    fetch_pr_refs as _fetch_pr_refs,
    detect_target_branch,
    get_base_commit,
    checkout_commit,
    run_command
)
from .config import PRInfo


def clone_repository(clone_url: str, dest_path: Path, logger: logging.Logger) -> bool:
    """Alias for clone_repo."""
    return clone_repo(clone_url, dest_path, logger)


def fetch_pr_refs(repo_path: Path, pr_number: int, logger: logging.Logger) -> bool:
    """
    Simplified fetch_pr_refs that works with just pr_number.

    Creates a minimal PRInfo object internally.
    """
    # Create minimal PRInfo - we only need pr_number and host
    from dataclasses import dataclass

    @dataclass
    class MinimalPRInfo:
        pr_number: int
        host: str = "github.com"

    pr_info = MinimalPRInfo(pr_number=pr_number)

    # Fetch and check if successful
    result = _fetch_pr_refs(repo_path, pr_info, logger)
    return result is not None


def get_pr_head_commit(repo_path: Path, pr_number: int, logger: logging.Logger) -> Optional[str]:
    """
    Get PR head commit SHA.

    This fetches the PR refs and returns the commit SHA.
    """
    from dataclasses import dataclass

    @dataclass
    class MinimalPRInfo:
        pr_number: int
        host: str = "github.com"

    pr_info = MinimalPRInfo(pr_number=pr_number)

    return _fetch_pr_refs(repo_path, pr_info, logger)
