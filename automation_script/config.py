"""
Configuration constants and settings for the automation workflow.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional


# =============================================================================
# Default Settings
# =============================================================================

DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 600  # 10 minutes per command
VENV_NAME = ".venv"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

# Directory structure constants
ARTIFACTS_DIR = "artifacts"
DOCKER_IMAGES_DIR = "docker_images"
PATCHES_DIR = "patches"
METADATA_DIR = "metadata"
LOGS_DIR = "logs"
UNIFIED_LOG_FILE = "workflow.log"  # Single unified log file for entire workflow

# Docker-specific settings
DOCKER_MEMORY_LIMIT = "8g"
DOCKER_CPU_LIMIT = "4"
DOCKER_BUILD_JOBS = "8"        # Number of parallel jobs for build tools (make -j, etc.)
DOCKER_TIMEOUT_BUILD = 1200  # 20 minutes for build
DOCKER_TIMEOUT_RUN = 1800    # 30 minutes for test run (increased for large test suites)

# =============================================================================
# Base Images - Ubuntu 24.04 LTS
# =============================================================================
# All Dockerfiles use Ubuntu LTS as base for consistency.
# Language toolchains are installed inside the container.
# The Dockerfile is self-contained with all dependencies pre-installed.
#
# Base Image: Ubuntu 24.04 LTS (Noble)
#
# Directory Structure:
#   /app/repo           - Repository source code
#   /saved/ENV          - Language toolchain and cached dependencies
#   /saved/venv/ENV     - Python virtual environment (Python projects only)
#   /workspace          - Evaluation workspace for results output

DOCKER_BASE_IMAGE = "ubuntu:24.04"                      # Ubuntu 24.04 LTS
DOCKER_BASE_IMAGE_UBUNTU_22 = "ubuntu:22.04"            # Ubuntu 22.04 LTS (Jammy) - legacy
DOCKER_BASE_IMAGE_UBUNTU_24 = "ubuntu:24.04"            # Ubuntu 24.04 LTS (Noble)

# Legacy aliases for backward compatibility (deprecated, use DOCKER_BASE_IMAGE)
DOCKER_BASE_IMAGE_PYTHON = "ubuntu:24.04"               # Now uses Ubuntu + venv at /saved/venv/ENV
DOCKER_BASE_IMAGE_NODE = "ubuntu:24.04"                 # Now uses Ubuntu + Node.js installed
DOCKER_BASE_IMAGE_GO = "ubuntu:24.04"                   # Now uses Ubuntu + Go installed
DOCKER_BASE_IMAGE_RUST = "ubuntu:24.04"                 # Now uses Ubuntu + rustup at /saved/ENV
DOCKER_BASE_IMAGE_DOTNET = "ubuntu:24.04"               # Now uses Ubuntu + dotnet SDK
DOCKER_BASE_IMAGE_RUBY = "ubuntu:24.04"                 # Now uses Ubuntu + Ruby installed
DOCKER_BASE_IMAGE_JAVA = "ubuntu:24.04"                 # Now uses Ubuntu + JDK/Maven
DOCKER_BASE_IMAGE_NIX = "nixos/nix:latest"              # NixOS still uses its own base

# Container paths (new standardized structure)
CONTAINER_REPO_PATH = "/app/repo"                       # Repository source code
CONTAINER_ENV_PATH = "/saved/ENV"                       # Language toolchain
CONTAINER_VENV_PATH = "/saved/venv/ENV"                 # Python virtual environment
CONTAINER_WORKSPACE_PATH = "/workspace"                 # Evaluation workspace

# Architecture build settings
# Multi-architecture builds (amd64 + arm64)
DOCKER_TARGET_PLATFORMS = ["linux/amd64", "linux/arm64"]  # Target platforms for buildx
DOCKER_BUILDX_BUILDER_NAME = "velora-builder"  # Kept for potential future use
DOCKER_USE_MULTIARCH = True  # Build and export universal amd64+arm64 archives

# OCI Image metadata (Jaeger project)
DOCKER_IMAGE_AUTHORS = "https://www.ethara.ai/"
DOCKER_DEFAULT_REPO_URL = "https://github.com/jaegertracing/jaeger.git"


# =============================================================================
# Language Detection
# =============================================================================

# Files that indicate a specific language
# Note: Order matters for polyglot repos - more specific patterns should come first
DEPENDENCY_FILES: Dict[str, List[str]] = {
    "python": ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile", "setup.cfg"],
    "javascript": ["package.json"],
    "typescript": ["package.json", "tsconfig.json"],
    "go": ["go.mod", "go.sum"],
    "rust": ["Cargo.toml"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "ruby": ["Gemfile"],
    "csharp": ["*.csproj", "*.sln"],
    "php": ["composer.json", "composer.lock"],
    # C/autoconf projects - check last since configure.ac is less common
    "c": ["configure.ac", "configure", "Makefile.am", "CMakeLists.txt"],
}

# File extensions mapped to languages
EXTENSION_TO_LANGUAGE: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".cs": "csharp",
    ".php": "php",
    ".c": "c",
    ".h": "c",
}


# =============================================================================
# Test Commands
# =============================================================================

# Default test commands by language
TEST_COMMANDS: Dict[str, List[str]] = {
    "python": ["pytest", "python -m pytest", "python -m unittest discover"],
    "javascript": ["npm test", "yarn test", "npx jest"],
    "typescript": ["npm test", "yarn test", "npx jest"],
    "go": ["go test ./..."],
    "rust": ["cargo test"],
    "java": ["mvn test -Dcheckstyle.skip=true -Dspotbugs.skip=true -Dpmd.skip=true -Dfindbugs.skip=true -Drat.skip=true -Denforcer.skip=true -Dlicense.skip=true -Djacoco.skip=true", "gradle test"],
    "ruby": ["bundle exec rspec", "rake test"],
    "csharp": ["dotnet test", "dotnet test --no-build"],
    "php": ["./vendor/bin/phpunit", "composer test", "phpunit"],
    "c": ["make test", "make check", "make test-all"],
}

# Test file patterns by language (to identify test files)
TEST_FILE_PATTERNS: Dict[str, List[str]] = {
    "python": ["test_*.py", "*_test.py", "tests/*.py", "test/*.py"],
    "javascript": ["*.test.js", "*.spec.js", "__tests__/*.js"],
    "typescript": ["*.test.ts", "*.spec.ts", "__tests__/*.ts"],
    "go": ["*_test.go"],
    "rust": ["tests/*.rs"],
    "java": ["*Test.java", "*Tests.java"],
    "ruby": ["*_spec.rb", "*_test.rb"],
    "csharp": ["*Tests.cs", "*Test.cs", "*.Tests.csproj"],
    "php": ["*Test.php", "*Tests.php", "tests/*.php", "test/*.php"],
    "c": ["test/*.c", "tests/*.c", "test_*.c", "*_test.c"],
}


# =============================================================================
# Environment Healing
# =============================================================================

# Strategies for fixing environment issues
HEALING_STRATEGIES = [
    "reinstall_deps",      # Force reinstall all dependencies
    "install_missing",     # Install specifically missing modules
    "clear_cache",         # Clear pip/npm cache
    "pin_versions",        # Install with version pinning
    "rebuild_wheels",      # Rebuild binary wheels
    "set_env_vars",        # Set common environment variables
]

# Common environment errors and their fixes
ERROR_PATTERNS: Dict[str, str] = {
    r"No module named '(\w+)'": "install_missing",
    r"ModuleNotFoundError": "reinstall_deps",
    r"ImportError": "reinstall_deps",
    r"FileNotFoundError": "check_files",
    r"PermissionError": "fix_permissions",
    r"ConnectionError": "network_retry",
    r"TimeoutError": "increase_timeout",
    r"OSError.*Errno": "os_error",
}


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class PRInfo:
    """Parsed information from a PR URL."""
    host: str           # e.g., "github.com"
    owner: str          # e.g., "facebook"
    repo: str           # e.g., "react"
    pr_number: int      # e.g., 123
    clone_url: str      # e.g., "https://github.com/facebook/react.git"
    api_url: str        # API endpoint for PR details


@dataclass
class TestResult:
    """Result of a single test run."""
    success: bool                           # Overall success
    exit_code: int                          # Process exit code
    stdout: str                             # Standard output
    stderr: str                             # Standard error
    duration: float                         # Execution time in seconds
    tests_passed: List[str] = field(default_factory=list)   # List of passed test names
    tests_failed: List[str] = field(default_factory=list)   # List of failed test names
    tests_skipped: List[str] = field(default_factory=list)  # List of skipped test names
    error_type: Optional[str] = None        # Type of error if environment issue


@dataclass
class WorkspaceConfig:
    """Workspace directory configuration."""
    root: Path
    repo: Path
    artifacts_base: Path
    artifacts_pr: Path
    docker_images: Path
    metadata: Path
    logs: Path

    @classmethod
    def create(cls, workspace_root: Path) -> "WorkspaceConfig":
        """Create workspace configuration from root path."""
        root = workspace_root.resolve()
        return cls(
            root=root,
            repo=root / "repo",
            artifacts_base=root / "artifacts" / "base",
            artifacts_pr=root / "artifacts" / "pr",
            docker_images=root / "docker_images",
            metadata=root / "metadata",
            logs=root / "logs",
        )

    def create_directories(self) -> None:
        """Create all workspace directories."""
        for path in [
            self.root,
            self.repo.parent,  # Don't create repo yet, clone will do it
            self.artifacts_base,
            self.artifacts_pr,
            self.docker_images,
            self.metadata,
            self.logs,
        ]:
            path.mkdir(parents=True, exist_ok=True)


@dataclass
class WorkflowMetadata:
    """
    Output metadata schema - exactly matches the required format.
    """
    instance_id: str = ""
    repo: str = ""
    base_commit: str = ""
    problem_statement: str = ""
    hints_text: str = ""
    FAIL_TO_PASS: str = ""      # JSON string of test list
    PASS_TO_PASS: str = ""      # JSON string of test list
    language: str = ""
    test_command: str = ""
    test_output_parser: str = ""
    image_storage_uri: str = ""
    patch: str = ""
    test_patch: str = ""
