"""
Language and test command detection for repositories.
"""

import logging
from pathlib import Path
from typing import Tuple, Optional, List

# Import from environment.py
from .environment import (
    detect_language,
    detect_test_command
)


def detect_language_and_test_command(
    repo_path: Path,
    logger: logging.Logger,
    changed_files: Optional[List[str]] = None,
    repo_full_name: Optional[str] = None
) -> Tuple[str, str]:
    """
    Detect repository language and test command.

    For polyglot repositories, providing changed_files enables more accurate
    language detection based on the files actually modified in the PR.

    Args:
        repo_path: Path to repository
        logger: Logger instance
        changed_files: Optional list of files changed in PR (for accurate detection)
        repo_full_name: Optional full repository name (e.g., "owner/repo") for config lookup

    Returns:
        Tuple of (language, test_command)
    """
    language = detect_language(repo_path, logger, changed_files=changed_files, repo_full_name=repo_full_name)
    test_command = detect_test_command(repo_path, language, logger, repo_full_name=repo_full_name, changed_files=changed_files)

    return language, test_command
