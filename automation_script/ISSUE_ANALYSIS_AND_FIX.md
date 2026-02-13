# Issue Analysis: Deno PR #31952 - Zero Tests Detected

## Problem Summary

The automation script successfully processed PR #31952 from denoland/deno but reported:
- ✅ Docker image built (5.4 GB)
- ✅ Patch generated and verified
- ❌ **0 FAIL_TO_PASS tests**
- ❌ **0 PASS_TO_PASS tests**

## Root Causes

### 1. **Missing Build Dependencies in Docker Image**
**Error**: `cargo test` failed with:
```
failed to find tool "c++": No such file or directory
is `cmake` not installed?
```

**Cause**: The generated Dockerfile used `rustlang/rust:nightly-slim` which lacks essential build tools:
- ❌ cmake (required for building native dependencies)
- ❌ g++/build-essential (C++ compiler)
- ❌ protobuf-compiler (needed by many Rust projects)
- ❌ Other system libraries

**Impact**: Both BASE and PATCHED tests failed to compile, resulting in exit code 101.

### 2. **Wrong Test Command for Deno**
**Issue**: Script used `cargo test` which:
- Tries to compile the entire Deno codebase (massive, requires complete build toolchain)
- Doesn't run Deno's integration tests (uses custom test framework)
- Misses the actual tests added in the PR (`.js` files in `tests/specs/run/`)

**Cause**: No special handling for Deno, which is a complex polyglot project with custom test infrastructure.

### 3. **Language Detection Limitations**
**Current**: Detects "rust" based on file extensions (correct for primary language)
**Missing**: No recognition of:
- Projects with custom test frameworks
- Repository-specific quirks
- Build requirement complexity

## Comprehensive Solution

### Fix 1: Enhanced Rust Dockerfile with Build Dependencies

**File**: `automation_script/docker_builder_new.py`

Add comprehensive build dependencies to the Rust Dockerfile generation:

```python
def generate_rust_dockerfile(repo_path: Path, logger: logging.Logger) -> Path:
    # ... existing version detection code ...
    
    # Detect if this is a complex project requiring additional build tools
    requires_build_tools = detect_rust_build_requirements(repo_path, logger)
    
    # Base packages
    base_packages = [
        "git",
        "python3",
        "python3-pip",
        "pkg-config",
        "libssl-dev"
    ]
    
    # Additional build tools for complex projects
    if requires_build_tools:
        base_packages.extend([
            "cmake",
            "build-essential",  # Provides g++, make, etc.
            "protobuf-compiler",
            "libprotobuf-dev",
            "clang",
            "libclang-dev"
        ])
    
    packages_str = " \\\n    ".join(base_packages)
    
    dockerfile_content = f"""# Rust project Docker image
FROM {rust_base_image}

WORKDIR /repo

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {packages_str} \\
    && rm -rf /var/lib/apt/lists/*

# Rest of Dockerfile...
"""
    # ... rest of function ...


def detect_rust_build_requirements(repo_path: Path, logger: logging.Logger) -> bool:
    """
    Detect if a Rust project requires additional build tools (cmake, g++, etc.).
    
    Returns True if project likely needs extra build dependencies.
    """
    # Check Cargo.lock for dependencies that require native builds
    cargo_lock = repo_path / "Cargo.lock"
    if cargo_lock.exists():
        try:
            with open(cargo_lock, 'r') as f:
                content = f.read()
                # Common crates that require cmake/C++ compiler
                native_build_indicators = [
                    'cmake',
                    'libz-sys',
                    'openssl-sys',
                    'ring',
                    'rocksdb',
                    'aws-lc-sys',
                    'protobuf',
                    'prost-build',
                    'bindgen'
                ]
                for indicator in native_build_indicators:
                    if indicator in content:
                        logger.info(f"Detected {indicator} in Cargo.lock - enabling build tools")
                        return True
        except Exception as e:
            logger.warning(f"Failed to check Cargo.lock: {e}")
    
    # Check for build.rs files (indicates custom build scripts)
    build_scripts = list(repo_path.rglob("build.rs"))
    if len(build_scripts) > 5:  # Threshold for "complex" project
        logger.info(f"Found {len(build_scripts)} build.rs files - enabling build tools")
        return True
    
    return False
```

### Fix 2: Repository-Specific Configuration System

**New File**: `automation_script/repo_configs.py`

```python
"""
Repository-specific configurations for known complex projects.

This module handles special cases where projects require custom
test commands, build configurations, or other specific handling.
"""

from dataclasses import dataclass
from typing import Optional, List
import re


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
        notes="Deno is a large Rust/TypeScript runtime. Only run unit tests, not full integration tests."
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
        notes="Polyglot repo with primary .NET SDK"
    ),
    # Add more as needed...
]


def get_repo_config(repo_full_name: str) -> Optional[RepoConfig]:
    """
    Get configuration for a repository if it has special handling.
    
    Args:
        repo_full_name: Full repo name like "owner/repo"
    
    Returns:
        RepoConfig if found, None otherwise
    """
    for config in REPO_CONFIGS:
        if re.match(config.repo_pattern, repo_full_name, re.IGNORECASE):
            return config
    return None
```

### Fix 3: Integrate Repository Configs into Language Detection

**File**: `automation_script/environment.py`

```python
from .repo_configs import get_repo_config

def detect_language(
    repo_path: Path,
    logger: logging.Logger,
    changed_files: Optional[List[str]] = None,
    repo_full_name: Optional[str] = None  # NEW parameter
) -> str:
    """
    Detect the primary programming language of the repository.
    
    NEW: Checks for repository-specific config first.
    """
    logger.info("Detecting repository language")
    
    # NEW: Check for repo-specific configuration first
    if repo_full_name:
        repo_config = get_repo_config(repo_full_name)
        if repo_config and repo_config.language:
            logger.info(f"Using configured language for {repo_full_name}: {repo_config.language}")
            return repo_config.language
    
    # ... rest of existing detection logic ...


def detect_test_command(
    repo_path: Path,
    language: str,
    logger: logging.Logger,
    repo_full_name: Optional[str] = None  # NEW parameter
) -> str:
    """
    Detect the appropriate test command for the repository.
    
    NEW: Checks for repository-specific test command first.
    """
    logger.info("Detecting test command")
    
    # NEW: Check for repo-specific configuration first
    if repo_full_name:
        repo_config = get_repo_config(repo_full_name)
        if repo_config and repo_config.test_command:
            logger.info(f"Using configured test command for {repo_full_name}: {repo_config.test_command}")
            return repo_config.test_command
    
    # ... rest of existing detection logic ...
```

### Fix 4: Enhanced Rust Dockerfile Generation

**File**: `automation_script/docker_builder_new.py`

```python
from .repo_configs import get_repo_config

def generate_rust_dockerfile(
    repo_path: Path, 
    logger: logging.Logger,
    repo_full_name: Optional[str] = None  # NEW parameter
) -> Path:
    """Generate optimized Dockerfile for Rust projects."""
    
    # Check for repo-specific config
    repo_config = get_repo_config(repo_full_name) if repo_full_name else None
    
    # Determine if we need build tools
    requires_build_tools = (
        (repo_config and repo_config.requires_build_tools) or
        detect_rust_build_requirements(repo_path, logger)
    )
    
    # Determine if we should skip full build
    skip_full_build = repo_config and repo_config.skip_full_build
    
    # ... version detection code ...
    
    # Build package list
    base_packages = ["git", "python3", "python3-pip", "pkg-config", "libssl-dev"]
    
    if requires_build_tools:
        logger.info("Enabling additional build tools (cmake, g++, etc.)")
        base_packages.extend([
            "cmake",
            "build-essential",
            "protobuf-compiler",
            "libprotobuf-dev"
        ])
    
    packages_str = " \\\n    ".join(base_packages)
    
    # Conditional build step
    if skip_full_build:
        build_step = "# Skipping full project build (configured for this repo)"
        logger.info("Skipping full project build in Dockerfile (configured)")
    else:
        build_step = """# Build the project (to verify it compiles)
RUN cargo build --release 2>/dev/null || cargo build 2>/dev/null || true"""
    
    dockerfile_content = f"""# Rust project Docker image
FROM {rust_base_image}

WORKDIR /repo

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {packages_str} \\
    && rm -rf /var/lib/apt/lists/*

# Copy Cargo files first for dependency caching
COPY Cargo.toml Cargo.lock* ./

# Create src/lib.rs placeholder for dependency download
RUN mkdir -p src && echo "fn main() {{}}" > src/main.rs && echo "" > src/lib.rs

# Download dependencies (this layer will be cached)
RUN cargo fetch || true
RUN cargo build --release 2>/dev/null || true

# Remove placeholder
RUN rm -rf src

# Copy full source
COPY . /repo/

{build_step}

# Remove automation_script from repo if it exists
RUN rm -rf /repo/automation_script || true

WORKDIR /repo

CMD ["cargo", "test"]
"""
    
    # ... write file ...
```

### Fix 5: Update Part 1 to Pass Repo Name

**File**: `automation_script/part1_build_and_base.py`

```python
# When calling language detection:
language, test_command = detect_language_and_test_command(
    repo_path=workspace.repo,
    logger=logger,
    changed_files=changed_files,
    repo_full_name=f"{pr_info.owner}/{pr_info.repo}"  # NEW
)

# When building Docker image:
image_tag = build_docker_image(
    repo_path=workspace.repo,
    language=language,
    base_commit=base_commit,
    pr_number=pr_info.pr_number,
    logger=logger,
    repo_full_name=f"{pr_info.owner}/{pr_info.repo}"  # NEW
)
```

## Implementation Priority

### Phase 1: Critical Fixes (Immediate)
1. ✅ Add build dependency detection to Rust Dockerfile
2. ✅ Create repo_configs.py with Deno configuration
3. ✅ Integrate repo configs into language/test detection

### Phase 2: Enhanced Detection (Short-term)
4. Add automatic detection of cmake/build requirements from Cargo.lock
5. Add fallback test commands when primary fails
6. Improve error messages for build failures

### Phase 3: Scalability (Long-term)
7. Create config file format (YAML/JSON) for easy additions
8. Add more known repositories to config
9. Implement test result validation (detect when tests don't run)

## Testing the Fix

After implementing, test with:

```bash
# Test with Deno PR
python -m automation_script.main_orchestrator \
  https://github.com/denoland/deno/pull/31952 \
  /tmp/test_deno_pr

# Verify:
# 1. Docker builds successfully (no cmake errors)
# 2. Tests run (even if some fail)
# 3. Test results are parsed
```

## Prevention for Future Issues

### 1. Pre-flight Checks
Add validation before running tests:
```python
def validate_docker_image(image_tag: str, test_command: str, logger: logging.Logger) -> bool:
    """Run a simple test to ensure Docker image can execute test command."""
    cmd = ["docker", "run", "--rm", image_tag, "sh", "-c", f"{test_command} --help || echo OK"]
    exit_code, stdout, stderr = run_command(cmd, logger=logger, timeout=30)
    if "command not found" in stderr.lower():
        logger.error(f"Test command '{test_command}' not found in image")
        return False
    return True
```

### 2. Test Result Validation
Add checks after test execution:
```python
def validate_test_results(result: TestResult, logger: logging.Logger) -> bool:
    """Validate that test results are meaningful."""
    if result.exit_code != 0 and len(result.tests_passed) == 0 and len(result.tests_failed) == 0:
        logger.warning("Tests failed but no test names captured - possible build/parse error")
        return False
    return True
```

### 3. Progressive Fallbacks
Implement fallback strategies:
```python
def run_tests_with_fallback(image_tag, test_commands, logger):
    """Try multiple test commands until one works."""
    for test_cmd in test_commands:
        logger.info(f"Attempting test command: {test_cmd}")
        result = run_tests(image_tag, test_cmd, logger)
        if result.exit_code == 0 or (result.tests_passed or result.tests_failed):
            return result
    logger.error("All test commands failed")
    return None
```

## Summary

**Root Cause**: Missing build dependencies + wrong test command for complex Rust project

**Solution**: 
1. Enhanced Dockerfile with build tool detection
2. Repository-specific configuration system
3. Better integration between components
4. Validation and fallback mechanisms

**Expected Outcome**: 
- Deno and similar complex projects will build successfully
- Tests will run and be properly categorized
- System is extensible for future edge cases
