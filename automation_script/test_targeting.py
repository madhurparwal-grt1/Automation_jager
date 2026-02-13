"""
Intelligent test targeting based on changed files.

This module analyzes changed files in a PR and generates targeted test commands
to run only relevant tests instead of the entire test suite.
"""

import logging
import json
from pathlib import Path
from typing import List, Set, Optional


def get_go_package_paths_from_files(changed_files: List[str], repo_path: Path, logger: logging.Logger) -> List[str]:
    """
    Extract Go package paths from changed files.
    
    Args:
        changed_files: List of changed file paths (relative to repo root)
        repo_path: Path to repository root
        logger: Logger instance
    
    Returns:
        List of Go package paths to test (e.g., "./staging/src/k8s.io/client-go/plugin/...")
    """
    
    package_dirs: Set[str] = set()
    test_dirs: Set[str] = set()
    filtered_files: List[str] = []
    
    # File patterns to exclude from test targeting
    exclude_patterns = [
        'vendor/',
        'node_modules/',
        '.github/',
        'docs/',
        'examples/',
        'hack/',
        'build/',
        'scripts/',
        '.md',
        '.txt',
        '.yaml',
        '.yml',
        '.json',
        '.sh',
        'Dockerfile',
        'Makefile',
        'LICENSE',
        'OWNERS',
        'CODEOWNERS'
    ]
    
    for file_path in changed_files:
        # Skip excluded patterns
        if any(pattern in file_path for pattern in exclude_patterns):
            filtered_files.append(file_path)
            continue
        
        # Only process Go files
        if not file_path.endswith('.go'):
            filtered_files.append(file_path)
            continue
        
        # Get the directory containing the file
        file_dir = str(Path(file_path).parent)
        
        # Normalize path separators
        file_dir = file_dir.replace('\\', '/')
        
        # Add to appropriate set
        if '_test.go' in file_path or '/test/' in file_path or file_path.startswith('test/'):
            test_dirs.add(file_dir)
        else:
            package_dirs.add(file_dir)
    
    
    # Combine all directories (package dirs will test their associated test files)
    all_dirs = package_dirs.union(test_dirs)
    
    if not all_dirs:
        logger.warning("No Go source directories found in changed files")
        return []
    
    # Convert to Go package paths with recursive test pattern
    package_paths = []
    for dir_path in sorted(all_dirs):
        # Ensure path starts with ./
        if not dir_path.startswith('./'):
            dir_path = './' + dir_path
        # Add recursive test pattern
        package_path = f"{dir_path}/..."
        package_paths.append(package_path)
    
    logger.info(f"Identified {len(package_paths)} Go package(s) to test")
    logger.info(f"Filtered out {len(filtered_files)} non-test files (docs, configs, etc.)")
    
    return package_paths


def generate_targeted_test_command(
    language: str,
    changed_files: List[str],
    repo_path: Path,
    default_command: str,
    logger: logging.Logger,
    max_targets: int = 20
) -> str:
    """
    Generate a targeted test command based on changed files.
    
    For large monorepos, this reduces test time by only running tests
    for packages that were actually modified.
    
    Args:
        language: Programming language of the repository
        changed_files: List of changed file paths
        repo_path: Path to repository root
        default_command: Default test command to use as fallback
        logger: Logger instance
        max_targets: Maximum number of test targets before falling back to default
    
    Returns:
        Test command string (targeted or default)
    """
    
    if not changed_files:
        logger.warning("No changed files provided, using default test command")
        return default_command
    
    # Currently only implemented for Go
    if language == "go":
        package_paths = get_go_package_paths_from_files(changed_files, repo_path, logger)
        
        if not package_paths:
            logger.info("No Go packages identified, using default test command")
            return default_command
        
        # If too many targets, fall back to default (might be too broad a change)
        if len(package_paths) > max_targets:
            logger.warning(f"Too many test targets ({len(package_paths)} > {max_targets}), using default test command")
            return default_command
        
        # Generate targeted Go test command
        # go test accepts multiple package paths separated by spaces
        test_command = f"go test -v {' '.join(package_paths)}"
        
        logger.info(f"Generated targeted test command: {test_command}")
        
        return test_command
    
    # For other languages, return default for now
    logger.info(f"Targeted testing not yet implemented for {language}, using default")
    return default_command
