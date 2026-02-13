"""
Repository-specific configurations for known complex projects.

This module handles special cases where projects require custom
test commands, build configurations, or other specific handling.
"""

from dataclasses import dataclass
from typing import Optional
import re
import logging


@dataclass
class RepoConfig:
    """Configuration for a specific repository."""
    repo_pattern: str  # Regex pattern to match repo (e.g., "denoland/deno")
    language: Optional[str] = None  # Override detected language
    test_command: Optional[str] = None  # Custom test command
    build_timeout: Optional[int] = None  # Custom build timeout (seconds)
    test_timeout: Optional[int] = None  # Custom test timeout (seconds)
    requires_build_tools: bool = False  # Force enable cmake, g++, etc.
    skip_full_build: bool = False  # Skip full project build in Dockerfile
    docker_extra_packages: Optional[list] = None  # Additional apt packages
    notes: str = ""  # Human-readable notes about this config


# Known repository configurations
REPO_CONFIGS = [
    RepoConfig(
        repo_pattern=r"denoland/deno",
        language="rust",  # Keep as rust but with special handling
        test_command="cargo test --lib --bins",  # Test only library/binaries, not integration
        build_timeout=2400,  # 40 minutes for large project
        test_timeout=1800,   # 30 minutes for tests
        requires_build_tools=True,
        skip_full_build=True,  # Don't build entire Deno in Dockerfile (too large)
        docker_extra_packages=["cmake", "build-essential", "protobuf-compiler", "libprotobuf-dev", "clang", "libclang-dev"],
        notes="Deno is a large Rust/TypeScript runtime with complex native dependencies. "
              "Requires libclang-dev for bindgen (used by libsqlite3-sys and others). "
              "Only run unit tests to avoid long build times."
    ),
    RepoConfig(
        repo_pattern=r"rust-lang/rust",
        test_command="python3 x.py test --stage 0",  # Use Rust's custom test harness
        build_timeout=3600,
        requires_build_tools=True,
        skip_full_build=True,
        notes="Rust compiler itself - uses custom x.py build system"
    ),
    RepoConfig(
        repo_pattern=r"microsoft/semantic-kernel",
        language="csharp",  # Even though it's polyglot
        notes="Polyglot repo with primary .NET SDK in dotnet/ subdirectory"
    ),
    RepoConfig(
        repo_pattern=r".*/tikv",
        requires_build_tools=True,
        docker_extra_packages=["cmake", "build-essential"],
        notes="TiKV requires CMake for RocksDB and other native dependencies"
    ),
    RepoConfig(
        repo_pattern=r"aws/aws-sdk-java-v2",
        language="java",
        test_command=None,  # Will be determined dynamically based on changed files
        build_timeout=2400,  # 40 minutes - large multi-module project
        test_timeout=2400,   # 40 minutes - many tests
        notes="AWS SDK for Java v2 - large multi-module Maven project. "
              "Test command should target specific modules based on changed files."
    ),
    RepoConfig(
        repo_pattern=r"concourse/concourse",
        language="go",
        test_command="go test -v ./fly/commands/... ./fly/commands/internal/interaction/... ./fly/commands/internal/setpipelinehelpers/... ./fly/integration/...",
        notes="Concourse has many integration tests. 'fly/integration' uses mocks and should run. 'testflight' requires a real cluster and is excluded."
    ),
    # Add more as needed...
]


def get_repo_config(repo_full_name: str, logger: Optional[logging.Logger] = None) -> Optional[RepoConfig]:
    """
    Get configuration for a repository if it has special handling.
    
    Args:
        repo_full_name: Full repo name like "owner/repo"
        logger: Optional logger instance
    
    Returns:
        RepoConfig if found, None otherwise
    """
    for config in REPO_CONFIGS:
        if re.match(config.repo_pattern, repo_full_name, re.IGNORECASE):
            if logger:
                logger.info(f"Found repository-specific config for: {repo_full_name}")
                if config.notes:
                    logger.info(f"Config notes: {config.notes}")
            return config
    return None


def detect_maven_modules_from_files(changed_files: list, logger: Optional[logging.Logger] = None) -> list:
    """
    Detect which Maven modules were changed based on file paths.
    
    For multi-module Maven projects, this identifies which sub-modules contain
    the changed files so we can target tests appropriately.
    
    Args:
        changed_files: List of changed file paths
        logger: Optional logger instance
    
    Returns:
        List of Maven module paths (e.g., ["services-custom/dynamodb-enhanced"])
    """
    modules = set()
    
    for file_path in changed_files:
        # Check if file is in a sub-module with pom.xml
        parts = file_path.split("/")
        
        # Look for patterns like "services-custom/dynamodb-enhanced/src/..."
        # We want to extract "services-custom/dynamodb-enhanced"
        for i in range(len(parts)):
            # Check if this level might be a module (contains src/ or pom.xml typically after it)
            if i < len(parts) - 1:
                if parts[i + 1] in ("src", "pom.xml", "target"):
                    # The module path is everything up to this point
                    module_path = "/".join(parts[:i + 1]) if i > 0 else parts[0]
                    if module_path and module_path != "src":
                        modules.add(module_path)
                        break
    
    modules_list = sorted(list(modules))
    if logger and modules_list:
        logger.info(f"Detected changed Maven modules: {modules_list}")
    
    return modules_list
