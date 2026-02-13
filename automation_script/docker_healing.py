"""
Docker Build and Test Retry/Healing Logic

This module implements retry strategies for Docker-based workflows:
- Docker build failures with dependency fixing
- Test execution failures with environment healing
- Error detection and classification
- Healing strategy application
"""

import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from .config import TestResult


# =============================================================================
# Error Detection
# =============================================================================

def detect_docker_build_error_type(stderr: str) -> Optional[str]:
    """
    Detect the type of Docker build error.

    Args:
        stderr: Docker build error output

    Returns:
        Error type string or None
    """
    stderr_lower = stderr.lower()

    # Check for Nix-specific libraries first (these require Nix, not apt)
    # These are NOT retriable as they need a completely different build environment
    nix_library_indicators = [
        'nix-flake-c', 'nix-cmd-c', 'nix-fetchers-c', 'nix-main-c',
        'nix-store-c', 'nix-expr-c', 'nixflake', 'nixcmd', 'nixfetchers',
        'nix-bindings', 'libnix',
    ]
    if any(indicator in stderr_lower for indicator in nix_library_indicators):
        if 'was not found' in stderr_lower or 'not found' in stderr_lower:
            return "requires_nix_package_manager"

    # Missing system library errors (pkg-config failures) - check early
    # These are common in Rust projects with *-sys crates
    if 'pkg-config' in stderr_lower and ('not found' in stderr_lower or 'was not found' in stderr_lower):
        # Check if it's a Nix library
        missing_lib = extract_missing_library(stderr)
        if missing_lib and is_non_apt_library(missing_lib):
            return "requires_nix_package_manager"
        return "missing_system_library"

    if 'the system library' in stderr_lower and 'was not found' in stderr_lower:
        # Check if it's a Nix library
        missing_lib = extract_missing_library(stderr)
        if missing_lib and is_non_apt_library(missing_lib):
            return "requires_nix_package_manager"
        return "missing_system_library"

    if 'pkg_config_path' in stderr_lower and 'needs to be installed' in stderr_lower:
        return "missing_system_library"

    # Rust edition2024/nightly requirement errors (check first - high priority)
    if 'edition2024' in stderr_lower or 'edition 2024' in stderr_lower:
        return "rust_edition2024_error"

    if 'feature' in stderr_lower and 'is required' in stderr_lower and 'not stabilized' in stderr_lower:
        return "rust_unstable_feature_error"

    # Network/connectivity errors
    if any(x in stderr_lower for x in ['connection refused', 'connection timed out',
                                        'temporary failure in name resolution',
                                        'could not resolve host']):
        return "network_error"

    # APT/Debian repository errors (often retriable)
    if any(x in stderr_lower for x in ['404  not found', 'failed to fetch',
                                        'does not have a release file',
                                        'some index files failed to download']):
        return "apt_repository_error"

    # Node.js/npm errors
    if 'npm err!' in stderr_lower:
        if 'network' in stderr_lower or 'enotfound' in stderr_lower:
            return "npm_network_error"
        if 'incompatible' in stderr_lower:
            return "node_version_error"
        return "npm_error"

    if 'yarn error' in stderr_lower:
        if 'the engine "node" is incompatible' in stderr_lower:
            return "node_version_error"
        return "yarn_error"

    # Go-specific errors
    if 'go: finding module' in stderr_lower or 'go: downloading' in stderr_lower:
        if 'connection' in stderr_lower or 'timeout' in stderr_lower:
            return "go_module_download_error"

    if 'cannot find package' in stderr_lower or 'no required module provides' in stderr_lower:
        return "go_missing_dependency"

    # Maven/Java errors
    if 'unsupportedclassversionerror' in stderr_lower:
        return "java_version_error"

    if 'maven' in stderr_lower and 'failed to execute goal' in stderr_lower:
        return "maven_plugin_error"

    # Dependency/package errors
    if any(x in stderr_lower for x in ['no such file or directory', 'not found',
                                        'error: failed to download']):
        return "missing_dependency"

    # Build/compilation errors
    if any(x in stderr_lower for x in ['syntax error', 'build failed',
                                        'compilation error']):
        return "compilation_error"

    # Out of memory
    if 'out of memory' in stderr_lower or 'cannot allocate memory' in stderr_lower:
        return "memory_error"

    # Disk space
    if 'no space left' in stderr_lower:
        return "disk_space_error"

    # Permission errors
    if 'permission denied' in stderr_lower:
        return "permission_error"

    # Docker tar error (usually from large .git directories)
    if 'write too long' in stderr_lower or 'archive/tar:' in stderr_lower:
        return "docker_context_error"

    return "unknown_error"


def detect_test_error_type(test_result: TestResult) -> Optional[str]:
    """
    Detect the type of test execution error.

    Args:
        test_result: Test execution result

    Returns:
        Error type string or None
    """
    combined = (test_result.stdout + test_result.stderr).lower()
    combined_original = test_result.stdout + test_result.stderr  # Keep case for some patterns

    # Already has error type
    if test_result.error_type:
        return test_result.error_type
    
    # CRITICAL CHECK: Zero tests ran - this is always an environment/build error
    # This must be checked early as it indicates something is fundamentally broken
    total_tests = len(test_result.tests_passed) + len(test_result.tests_failed) + len(test_result.tests_skipped)
    if total_tests == 0 and test_result.exit_code != 0:
        # Try to identify the specific cause
        if 'maven' in combined or 'mvn' in combined:
            if 'compilation failure' in combined or 'compile failure' in combined:
                return "zero_tests_compilation_error"
            if 'cannot resolve dependencies' in combined or 'could not find artifact' in combined:
                return "zero_tests_dependency_error"
            return "zero_tests_maven_error"
        if 'gradle' in combined:
            if 'compilejava failed' in combined or 'compilation failed' in combined:
                return "zero_tests_compilation_error"
            return "zero_tests_gradle_error"
        if 'cargo' in combined or 'rustc' in combined:
            return "zero_tests_rust_error"
        if 'npm' in combined or 'yarn' in combined or 'node' in combined:
            return "zero_tests_node_error"
        if 'pytest' in combined or 'python' in combined:
            return "zero_tests_python_error"
        if 'go test' in combined or 'go build' in combined:
            return "zero_tests_go_error"
        # Generic zero tests error
        return "zero_tests_unknown_error"

    # Check for Nix-specific libraries first (these require Nix, not apt)
    # These are NOT retriable as they need a completely different build environment
    nix_library_indicators = [
        'nix-flake-c', 'nix-cmd-c', 'nix-fetchers-c', 'nix-main-c',
        'nix-store-c', 'nix-expr-c', 'nixflake', 'nixcmd', 'nixfetchers',
        'nix-bindings', 'libnix',
    ]
    if any(indicator in combined for indicator in nix_library_indicators):
        if 'was not found' in combined or 'not found' in combined:
            return "requires_nix_package_manager"

    # Missing system library errors (pkg-config failures) - check early
    # These are common in Rust projects with *-sys crates
    if 'pkg-config' in combined and ('not found' in combined or 'was not found' in combined):
        # Double-check it's not a Nix library before marking as retriable
        missing_lib = extract_missing_library(combined_original)
        if missing_lib and is_non_apt_library(missing_lib):
            return "requires_nix_package_manager"
        return "missing_system_library"

    if 'the system library' in combined and 'was not found' in combined:
        # Double-check it's not a Nix library before marking as retriable
        missing_lib = extract_missing_library(combined_original)
        if missing_lib and is_non_apt_library(missing_lib):
            return "requires_nix_package_manager"
        return "missing_system_library"

    if 'pkg_config_path' in combined and 'needs to be installed' in combined:
        return "missing_system_library"

    # Rust edition2024/nightly requirement errors (check first - high priority)
    # These errors typically appear when cargo tries to download dependencies
    if 'edition2024' in combined or 'edition 2024' in combined:
        return "rust_edition2024_error"

    if 'feature' in combined and 'is required' in combined and 'not stabilized' in combined:
        return "rust_unstable_feature_error"

    # Rust toolchain version mismatch
    if 'rust-version' in combined and 'requires rustc' in combined:
        return "rust_version_mismatch"

    # Python environment errors
    if 'modulenotfounderror' in combined or 'no module named' in combined:
        return "missing_module"

    if 'importerror' in combined:
        return "import_error"

    # Java/Maven/Gradle specific errors
    if 'unsupportedclassversionerror' in combined:
        return "java_version_error"

    if 'class file version' in combined and 'this version of the java runtime only recognizes' in combined:
        return "java_version_error"

    if 'invalid source release' in combined or 'invalid target release' in combined:
        return "java_version_error"

    if 'source option' in combined and 'no longer supported' in combined:
        return "java_version_error"

    if 'java compilation initialization error' in combined:
        return "java_compilation_error"

    if 'execution failed for task' in combined and 'compilejava' in combined:
        return "java_compilation_error"

    if 'build failure' in combined and 'maven' in combined:
        # Check if it's a plugin execution failure vs actual test failure
        if 'failed to execute goal' in combined:
            if 'checkstyle' in combined or 'spotbugs' in combined or 'pmd' in combined:
                return "maven_plugin_error"
            return "maven_build_error"

    if 'cannot resolve dependencies' in combined or 'could not find artifact' in combined:
        return "maven_dependency_error"

    # Timeout errors - be more specific to avoid false positives from test names containing "timeout"
    # Look for actual timeout error messages, not just the word "timeout"
    timeout_indicators = [
        'timed out',
        'operation timed out',
        'connection timed out',
        'test timed out',
        'execution timed out',
        'deadline exceeded',
    ]
    if test_result.exit_code == 124 or any(indicator in combined for indicator in timeout_indicators):
        return "timeout_error"

    # Memory errors
    if 'out of memory' in combined or 'memoryerror' in combined:
        return "memory_error"

    # Network errors
    if any(x in combined for x in ['connection refused', 'connection error',
                                     'network unreachable']):
        return "network_error"

    # Go-specific test errors
    if 'panic:' in combined:
        return "go_panic"

    if 'race detected' in combined:
        return "go_race_condition"

    # Node.js/JavaScript errors
    if 'npm err!' in combined or 'yarn error' in combined:
        if 'enoent' in combined:
            return "npm_missing_file"
        if 'network' in combined:
            return "npm_network_error"
        return "npm_error"

    if 'the engine "node" is incompatible' in combined:
        return "node_version_error"

    # If exit code is non-zero but no specific error detected
    if test_result.exit_code != 0 and not test_result.success:
        # If tests actually ran (some passed or failed), it's likely not an environment issue
        # This handles cases where tests fail normally but contain generic words like 'error'
        if test_result.tests_passed and len(test_result.tests_passed) > 0:
            return None  # Tests ran successfully, just some failures - not environment issue
        
        if test_result.tests_failed and len(test_result.tests_failed) > 0:
            # Tests were detected, check for specific environment error patterns
            # These are more precise than just checking for 'error' or 'module'
            env_error_patterns = [
                'modulenotfounderror:', 'no module named',
                'importerror:', 'cannot import name',
                'panic: runtime error:', 'fatal error:',
                'connection refused', 'connection timed out',
                'network unreachable', 'failed to connect',
                'command not found', 'executable file not found',
                'permission denied:', 'no such file or directory:',
                'cannot find package', 'go: module',
                'error: linking with', 'linker command failed',
                'cannot open shared object file',
            ]
            
            # Only classify as environment error if specific patterns found
            if any(pattern in combined for pattern in env_error_patterns):
                return "test_execution_error"
            
            return None  # Just test failures, not environment issue
        
        # No tests detected at all - could be environment issue preventing test discovery
        return "test_execution_error"

    return None


def is_retriable_error(error_type: Optional[str]) -> bool:
    """
    Determine if an error is retriable.

    Args:
        error_type: Error type string

    Returns:
        True if error is retriable
    """
    if not error_type:
        return False

    # NON-retriable errors (require fundamentally different build environment)
    non_retriable = {
        'requires_nix_package_manager',  # Needs Nix, not apt - can't be healed
        'compilation_error',  # Code issues, not environment
        'unknown_error',  # Can't determine how to fix
        'zero_tests_compilation_error',  # Code compilation issues - can't be fixed by healing
    }
    
    if error_type in non_retriable:
        return False

    # Retriable errors
    retriable = {
        'network_error',
        'go_module_download_error',
        'timeout_error',
        'missing_dependency',
        'go_missing_dependency',
        'missing_module',
        'import_error',
        'apt_repository_error',  # May succeed with archive repos
        'npm_network_error',
        'maven_dependency_error',
        'docker_context_error',  # May succeed with .dockerignore fix
        'rust_edition2024_error',  # Can be healed by switching to nightly
        'rust_unstable_feature_error',  # Can be healed by switching to nightly
        'rust_version_mismatch',  # Can be healed by using appropriate Rust version
        'missing_system_library',  # Can be healed by adding system packages to Dockerfile
        # Zero tests errors - attempt healing by rebuilding/fixing deps
        'zero_tests_maven_error',  # Maven build issues - try healing
        'zero_tests_dependency_error',  # Dependency resolution issues
        'zero_tests_gradle_error',  # Gradle build issues
        'zero_tests_rust_error',  # Rust build issues
        'zero_tests_node_error',  # Node/npm build issues
        'zero_tests_python_error',  # Python import/setup issues
        'zero_tests_go_error',  # Go build issues
        'zero_tests_unknown_error',  # Try generic healing
    }

    return error_type in retriable


def is_zero_tests_error(error_type: Optional[str]) -> bool:
    """
    Check if the error indicates zero tests ran.
    
    Args:
        error_type: Error type string
        
    Returns:
        True if this is a zero tests error
    """
    if not error_type:
        return False
    return error_type.startswith('zero_tests_')


# =============================================================================
# System Library Mapping
# =============================================================================

# Libraries that CANNOT be installed via apt-get (require special handling)
# These are typically from external package managers like Nix, Homebrew, etc.
NON_APT_LIBRARIES = {
    # Nix package manager libraries - require Nix to be installed
    'nix-flake-c': 'nix',
    'nix-cmd-c': 'nix',
    'nix-fetchers-c': 'nix',
    'nix-main-c': 'nix',
    'nix-store-c': 'nix',
    'nix-expr-c': 'nix',
    'nixflake': 'nix',
    'nixcmd': 'nix',
    'nixfetchers': 'nix',
    'nixmainc': 'nix',
    'nixstore': 'nix',
    'nixexpr': 'nix',
}

# Mapping from pkg-config library names to Debian/Ubuntu package names
# This covers common Rust *-sys crates and their system dependencies
SYSTEM_LIBRARY_PACKAGES = {
    # FFmpeg libraries
    'libavutil': ['libavutil-dev', 'ffmpeg', 'libavcodec-dev', 'libavformat-dev', 'libswscale-dev', 'libswresample-dev', 'libavfilter-dev', 'libavdevice-dev'],
    'libavcodec': ['libavcodec-dev', 'ffmpeg', 'libavutil-dev'],
    'libavformat': ['libavformat-dev', 'ffmpeg', 'libavutil-dev', 'libavcodec-dev'],
    'libswscale': ['libswscale-dev', 'ffmpeg', 'libavutil-dev'],
    'libswresample': ['libswresample-dev', 'ffmpeg', 'libavutil-dev'],
    'libavfilter': ['libavfilter-dev', 'ffmpeg', 'libavutil-dev'],
    'libavdevice': ['libavdevice-dev', 'ffmpeg', 'libavutil-dev'],
    # OpenSSL
    'openssl': ['libssl-dev', 'pkg-config'],
    'libssl': ['libssl-dev', 'pkg-config'],
    'libcrypto': ['libssl-dev', 'pkg-config'],
    # Graphics/GUI libraries
    'x11': ['libx11-dev', 'libxext-dev', 'libxrender-dev'],
    'xcb': ['libxcb1-dev', 'libxcb-shm0-dev', 'libxcb-randr0-dev'],
    'wayland': ['libwayland-dev'],
    'gtk': ['libgtk-3-dev'],
    'gtk+-3.0': ['libgtk-3-dev'],
    'glib-2.0': ['libglib2.0-dev'],
    'pango': ['libpango1.0-dev'],
    'cairo': ['libcairo2-dev'],
    # Audio libraries
    'alsa': ['libasound2-dev'],
    'pulseaudio': ['libpulse-dev'],
    'jack': ['libjack-jackd2-dev'],
    # Database libraries
    'sqlite3': ['libsqlite3-dev'],
    'libpq': ['libpq-dev'],
    'mysqlclient': ['libmysqlclient-dev'],
    # Compression libraries
    'zlib': ['zlib1g-dev'],
    'bzip2': ['libbz2-dev'],
    'lzma': ['liblzma-dev'],
    'zstd': ['libzstd-dev'],
    # Network/Security libraries
    'libssh2': ['libssh2-1-dev'],
    'libcurl': ['libcurl4-openssl-dev'],
    'gnutls': ['libgnutls28-dev'],
    # Other common libraries
    'freetype2': ['libfreetype6-dev'],
    'fontconfig': ['libfontconfig1-dev'],
    'libffi': ['libffi-dev'],
    'libxml-2.0': ['libxml2-dev'],
    'libxslt': ['libxslt1-dev'],
    'libudev': ['libudev-dev'],
    'dbus-1': ['libdbus-1-dev'],
    'libpcre': ['libpcre3-dev'],
    'expat': ['libexpat1-dev'],
    # Video/Image libraries
    'libpng': ['libpng-dev'],
    'libjpeg': ['libjpeg-dev'],
    'libwebp': ['libwebp-dev'],
    'libvpx': ['libvpx-dev'],
    'libopus': ['libopus-dev'],
    'libx264': ['libx264-dev'],
    'libx265': ['libx265-dev'],
}

# Mapping from Rust crate names to required system packages
# This allows proactive detection before build failures
CRATE_SYSTEM_PACKAGES = {
    'ffmpeg-sys-next': ['libavutil-dev', 'libavcodec-dev', 'libavformat-dev', 'libswscale-dev', 'libswresample-dev', 'libavfilter-dev', 'libavdevice-dev', 'ffmpeg', 'pkg-config', 'clang', 'libclang-dev'],
    'ffmpeg-sys': ['libavutil-dev', 'libavcodec-dev', 'libavformat-dev', 'libswscale-dev', 'libswresample-dev', 'ffmpeg', 'pkg-config'],
    'openssl-sys': ['libssl-dev', 'pkg-config'],
    'libsqlite3-sys': ['libsqlite3-dev'],
    'libz-sys': ['zlib1g-dev'],
    'bzip2-sys': ['libbz2-dev'],
    'curl-sys': ['libcurl4-openssl-dev'],
    'freetype-sys': ['libfreetype6-dev'],
    'fontconfig-sys': ['libfontconfig1-dev'],
    'pango-sys': ['libpango1.0-dev'],
    'cairo-sys-rs': ['libcairo2-dev'],
    'glib-sys': ['libglib2.0-dev'],
    'gtk-sys': ['libgtk-3-dev'],
    'alsa-sys': ['libasound2-dev'],
    'libpulse-sys': ['libpulse-dev'],
    'x11': ['libx11-dev', 'libxext-dev'],
    'xcb': ['libxcb1-dev'],
    'wayland-sys': ['libwayland-dev'],
    'pq-sys': ['libpq-dev'],
    'mysqlclient-sys': ['libmysqlclient-dev'],
    'libssh2-sys': ['libssh2-1-dev'],
    'libgit2-sys': ['libssl-dev', 'pkg-config', 'cmake'],
    'libdbus-sys': ['libdbus-1-dev'],
    'libudev-sys': ['libudev-dev'],
    'expat-sys': ['libexpat1-dev'],
    'libxml': ['libxml2-dev'],
    'libxslt': ['libxslt1-dev'],
    'libpng-sys': ['libpng-dev'],
    'mozjpeg-sys': ['libjpeg-dev', 'nasm'],
    'libwebp-sys': ['libwebp-dev'],
    'vpx-sys': ['libvpx-dev'],
    'opus-sys': ['libopus-dev'],
    'x264-sys': ['libx264-dev'],
    'x265-sys': ['libx265-dev'],
    'clang-sys': ['clang', 'libclang-dev'],
    'bindgen': ['clang', 'libclang-dev'],
}


def extract_missing_library(stderr: str) -> Optional[str]:
    """
    Extract the name of the missing system library from error output.

    Args:
        stderr: Error output from build/test

    Returns:
        Library name (pkg-config name) or None
    """
    import re

    # Pattern: "The system library `libavutil` required by..."
    match = re.search(r"the system library [`']([^`']+)[`']", stderr.lower())
    if match:
        return match.group(1)

    # Pattern: "pkg-config --libs --cflags libavutil"
    match = re.search(r'pkg-config\s+[^\n]*\s+(\S+)\s*$', stderr, re.MULTILINE)
    if match:
        return match.group(1)

    # Pattern: "The file `libavutil.pc` needs to be installed"
    match = re.search(r"the file [`']([^`']+)\.pc[`']", stderr.lower())
    if match:
        return match.group(1)

    return None


def extract_missing_crate(stderr: str) -> Optional[str]:
    """
    Extract the name of the Rust crate that requires system libraries.

    Args:
        stderr: Error output from build/test

    Returns:
        Crate name or None
    """
    import re

    # Pattern: "required by crate `ffmpeg-sys-next`"
    match = re.search(r"required by crate [`']([^`']+)[`']", stderr.lower())
    if match:
        return match.group(1)

    # Pattern: "ffmpeg-sys-next v7.1.3"
    match = re.search(r"Compiling\s+(\S+-sys\S*)\s+v", stderr)
    if match:
        return match.group(1)

    return None


def is_non_apt_library(library_name: str) -> Optional[str]:
    """
    Check if a library requires a non-apt package manager (like Nix).

    Args:
        library_name: pkg-config library name (e.g., 'nix-flake-c')

    Returns:
        The required package manager name (e.g., 'nix') if not apt-compatible, None otherwise
    """
    # Normalize the library name for comparison
    normalized = library_name.lower().replace('-', '').replace('_', '')
    
    for lib_pattern, pkg_manager in NON_APT_LIBRARIES.items():
        pattern_normalized = lib_pattern.lower().replace('-', '').replace('_', '')
        if normalized == pattern_normalized or normalized.startswith(pattern_normalized.rstrip('c')):
            return pkg_manager
    
    # Check without 'lib' prefix
    if library_name.startswith('lib'):
        return is_non_apt_library(library_name[3:])
    
    return None


def get_packages_for_library(library_name: str) -> List[str]:
    """
    Get the apt package names needed for a library.

    Args:
        library_name: pkg-config library name (e.g., 'libavutil')

    Returns:
        List of apt package names to install.
        Returns empty list if the library requires non-apt installation (like Nix).
    """
    # First check if this is a non-apt library
    non_apt_manager = is_non_apt_library(library_name)
    if non_apt_manager:
        # Return empty list - this library can't be installed via apt
        return []

    # Direct lookup
    if library_name in SYSTEM_LIBRARY_PACKAGES:
        return SYSTEM_LIBRARY_PACKAGES[library_name]

    # Try without 'lib' prefix
    if library_name.startswith('lib'):
        short_name = library_name[3:]
        if short_name in SYSTEM_LIBRARY_PACKAGES:
            return SYSTEM_LIBRARY_PACKAGES[short_name]

    # Try with 'lib' prefix
    if not library_name.startswith('lib'):
        long_name = 'lib' + library_name
        if long_name in SYSTEM_LIBRARY_PACKAGES:
            return SYSTEM_LIBRARY_PACKAGES[long_name]

    # Generic fallback: try common naming patterns
    fallback = []
    if library_name.startswith('lib'):
        # libfoo -> libfoo-dev
        fallback.append(f"{library_name}-dev")
    else:
        # foo -> libfoo-dev
        fallback.append(f"lib{library_name}-dev")

    return fallback


def get_packages_for_crate(crate_name: str) -> List[str]:
    """
    Get the apt package names needed for a Rust crate.

    Args:
        crate_name: Rust crate name (e.g., 'ffmpeg-sys-next')

    Returns:
        List of apt package names to install
    """
    if crate_name in CRATE_SYSTEM_PACKAGES:
        return CRATE_SYSTEM_PACKAGES[crate_name]

    # Normalize crate name (remove version suffixes like -next)
    base_name = crate_name.replace('-next', '').replace('-rs', '').replace('-sys', '')

    # Try variations
    for variation in [crate_name, f"{crate_name}-sys", base_name, f"{base_name}-sys"]:
        if variation in CRATE_SYSTEM_PACKAGES:
            return CRATE_SYSTEM_PACKAGES[variation]

    return []


# =============================================================================
# Docker Build Healing
# =============================================================================

def apply_docker_build_healing(
    repo_path: Path,
    language: str,
    attempt: int,
    error_type: Optional[str],
    logger: logging.Logger,
    error_output: str = ""
) -> bool:
    """
    Apply healing strategy for Docker build failures.

    Strategies cycle through:
    1. Retry with cache cleared
    2. Add network retry for Go modules
    3. Use alternative base image or mirror
    4. Switch to nightly Rust for edition2024 requirements
    5. Add missing system libraries for pkg-config failures

    Args:
        repo_path: Path to repository
        language: Repository language
        attempt: Attempt number (0-indexed)
        error_type: Type of error detected
        logger: Logger instance
        error_output: Full error output for extracting details

    Returns:
        True if healing was applied
    """
    logger.info(f"Applying Docker build healing: attempt={attempt+1}, error={error_type}")

    # For missing system library errors
    if error_type == "missing_system_library":
        logger.info("Healing: Attempting to add missing system libraries to Dockerfile")
        return _heal_missing_system_library(repo_path, error_output, logger)

    # For Rust projects with edition2024 or unstable feature errors
    if language == "rust" and error_type in ["rust_edition2024_error", "rust_unstable_feature_error", "rust_version_mismatch"]:
        logger.info("Healing: Switching to nightly Rust for edition2024/unstable feature support")
        return _heal_rust_nightly_requirement(repo_path, logger)

    # For Go projects with network errors
    if language == "go" and error_type in ["network_error", "go_module_download_error"]:
        logger.info("Healing: Adding GOPROXY fallback and retry logic to Dockerfile")

        # Modify go.mod download command in Dockerfile to use proxy fallbacks
        dockerfile_path = repo_path / "Dockerfile.pr-eval"
        if dockerfile_path.exists():
            with open(dockerfile_path, 'r') as f:
                content = f.read()

            # Replace go mod download with retried version using multiple proxies
            if 'RUN go mod download' in content and 'GOPROXY' not in content:
                content = content.replace(
                    'RUN go mod download',
                    'ENV GOPROXY=https://proxy.golang.org,https://goproxy.io,direct\n'
                    'RUN go mod download'
                )

                with open(dockerfile_path, 'w') as f:
                    f.write(content)

                logger.info("Added GOPROXY fallback to Dockerfile")
                return True

    # For missing dependencies
    if error_type == "missing_dependency":
        logger.info("Healing: Will rebuild with --no-cache to fetch fresh dependencies")
        # This is handled by adding --no-cache flag in retry logic
        return True

    # Generic: Clear Docker build cache
    if attempt == 1:
        logger.info("Healing: Clearing Docker build cache")
        from .utils import run_command
        run_command(
            ["docker", "builder", "prune", "-f"],
            logger=logger,
            timeout=60
        )
        return True

    return False


def _heal_missing_system_library(repo_path: Path, error_output: str, logger: logging.Logger) -> bool:
    """
    Heal missing system library errors by adding packages to Dockerfile.

    This modifies the Dockerfile to install the missing system libraries.

    Args:
        repo_path: Path to repository
        error_output: Full error output containing library name
        logger: Logger instance

    Returns:
        True if healing was applied.
        Returns False if the library requires a non-apt package manager (like Nix).
    """
    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    if not dockerfile_path.exists():
        logger.warning("No Dockerfile.pr-eval found to heal")
        return False

    # Extract which library is missing
    missing_lib = extract_missing_library(error_output)
    missing_crate = extract_missing_crate(error_output)

    # Check if this is a non-apt library (requires Nix, etc.)
    if missing_lib:
        non_apt_manager = is_non_apt_library(missing_lib)
        if non_apt_manager:
            logger.error(f"❌ Library '{missing_lib}' requires {non_apt_manager.upper()} package manager (not apt)")
            logger.error(f"   This repository cannot be built with standard Docker images")
            logger.error(f"   The project requires the {non_apt_manager.upper()} package manager to be installed")
            logger.warning(f"   Marking this PR as incompatible with Docker-based testing")
            return False

    packages_to_add = []

    # Get packages based on library name
    if missing_lib:
        packages_to_add.extend(get_packages_for_library(missing_lib))
        logger.info(f"Missing library '{missing_lib}' requires packages: {packages_to_add}")

    # Get packages based on crate name (more comprehensive)
    if missing_crate:
        crate_packages = get_packages_for_crate(missing_crate)
        for pkg in crate_packages:
            if pkg not in packages_to_add:
                packages_to_add.append(pkg)
        logger.info(f"Crate '{missing_crate}' requires additional packages: {crate_packages}")

    if not packages_to_add:
        logger.warning(f"Could not determine packages for missing library: {missing_lib or 'unknown'}")
        # Try a generic fallback for common FFmpeg-related errors
        if 'ffmpeg' in error_output.lower() or 'libav' in error_output.lower():
            packages_to_add = ['ffmpeg', 'libavutil-dev', 'libavcodec-dev', 'libavformat-dev',
                              'libswscale-dev', 'libswresample-dev', 'libavfilter-dev',
                              'pkg-config', 'clang', 'libclang-dev']
            logger.info(f"Using FFmpeg fallback packages: {packages_to_add}")
        else:
            return False

    # Read current Dockerfile
    with open(dockerfile_path, 'r') as f:
        content = f.read()

    # Check if packages are already present
    already_present = all(pkg in content for pkg in packages_to_add)
    if already_present:
        logger.info("All required packages already in Dockerfile")
        return False

    # Find the apt-get install line and add our packages
    import re

    # Pattern to match apt-get install command with packages
    apt_pattern = r'(apt-get\s+install\s+-y\s+--no-install-recommends\s*\\?\n?\s*)([^&\n]+)'

    def add_packages(match):
        prefix = match.group(1)
        existing_packages = match.group(2)

        # Parse existing packages
        existing_list = [p.strip() for p in existing_packages.replace('\\', '').split() if p.strip() and p.strip() != '\\']

        # Add new packages that aren't already present
        for pkg in packages_to_add:
            if pkg not in existing_list:
                existing_list.append(pkg)

        # Format the new package list
        new_packages = ' \\\n    '.join(existing_list)
        return f"{prefix}{new_packages}"

    new_content, count = re.subn(apt_pattern, add_packages, content)

    if count == 0:
        # Fallback: Add a new RUN apt-get install command after the existing one
        logger.info("Could not find apt-get install pattern, adding new RUN command")

        # Look for "RUN apt-get update" or similar
        apt_update_pattern = r'(RUN\s+apt-get\s+update[^\n]*\n)'

        packages_str = ' \\\n    '.join(packages_to_add)
        new_install_cmd = f'\n# Install additional system libraries (auto-healed)\nRUN apt-get update && apt-get install -y --no-install-recommends \\\n    {packages_str} \\\n    && rm -rf /var/lib/apt/lists/*\n'

        if re.search(apt_update_pattern, content):
            # Insert after the last apt-get command block
            # Find the end of the existing apt block
            new_content = re.sub(
                r'(RUN\s+apt-get\s+update[^R]*rm -rf /var/lib/apt/lists/\*)',
                r'\1' + new_install_cmd,
                content,
                count=1
            )
            if new_content == content:
                # Simpler fallback: add before COPY command
                new_content = content.replace(
                    '# Copy full source',
                    new_install_cmd + '\n# Copy full source'
                )
        else:
            # Insert at the beginning of WORKDIR section
            new_content = content.replace(
                'WORKDIR /repo\n',
                'WORKDIR /repo\n' + new_install_cmd,
                1
            )

    if new_content == content:
        logger.warning("Failed to modify Dockerfile to add packages")
        return False

    # Write updated Dockerfile
    with open(dockerfile_path, 'w') as f:
        f.write(new_content)

    logger.info(f"✓ Dockerfile updated to include packages: {packages_to_add}")
    return True


def _heal_rust_nightly_requirement(repo_path: Path, logger: logging.Logger) -> bool:
    """
    Heal Rust projects that require nightly by regenerating Dockerfile with nightly base image.

    This is called when dependencies require edition2024 or unstable features.

    Args:
        repo_path: Path to repository
        logger: Logger instance

    Returns:
        True if healing was applied
    """
    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    if not dockerfile_path.exists():
        logger.warning("No Dockerfile.pr-eval found to heal")
        return False

    with open(dockerfile_path, 'r') as f:
        content = f.read()

    # Check if already using nightly
    if 'rustlang/rust:nightly' in content or 'rust:nightly' in content:
        logger.info("Already using nightly Rust, no change needed")
        return False

    # Find the current FROM line and replace with nightly
    import re

    # Pattern to match various Rust base images:
    # - rust:1.83-slim-bookworm
    # - rust:1.70-slim
    # - rust:stable
    rust_image_pattern = r'FROM\s+rust:[^\s]+'

    if re.search(rust_image_pattern, content):
        # Replace with nightly slim image
        new_content = re.sub(
            rust_image_pattern,
            'FROM rustlang/rust:nightly-slim',
            content
        )

        # Add a comment explaining why nightly is being used
        if '# Rust project Docker image' in new_content:
            new_content = new_content.replace(
                '# Rust project Docker image',
                '# Rust project Docker image\n# NOTE: Using nightly Rust due to dependencies requiring edition2024 or unstable features'
            )

        with open(dockerfile_path, 'w') as f:
            f.write(new_content)

        logger.info("✓ Dockerfile updated to use nightly Rust (rustlang/rust:nightly-slim)")
        logger.info("  This is required for dependencies using edition2024 or unstable features")
        return True
    else:
        logger.warning("Could not find Rust base image pattern in Dockerfile")
        return False


def _heal_zero_tests_error(
    repo_path: Path,
    language: str,
    attempt: int,
    error_type: str,
    test_result: TestResult,
    logger: logging.Logger
) -> Dict[str, Any]:
    """
    Attempt to heal zero tests errors by applying various fixes.
    
    Args:
        repo_path: Path to repository
        language: Repository language
        attempt: Attempt number (0-indexed)
        error_type: Specific zero tests error type
        test_result: Previous test result
        logger: Logger instance
        
    Returns:
        Dict of modifications for next attempt
    """
    modifications = {}
    error_output = test_result.stdout + test_result.stderr
    error_lower = error_output.lower()
    
    logger.info(f"Analyzing zero tests error: {error_type}")
    
    # Attempt 1: Check for missing dependencies and try to fix Dockerfile
    if attempt == 0:
        logger.info("Healing attempt 1: Checking for missing dependencies...")
        
        # Check for Maven dependency issues
        if error_type in ["zero_tests_maven_error", "zero_tests_dependency_error"]:
            # Check for common Maven issues in the output
            if 'could not find artifact' in error_lower or 'cannot resolve dependencies' in error_lower:
                logger.info("Detected Maven dependency resolution failure")
                # Try rebuilding with fresh dependencies
                modifications['rebuild_docker_image'] = True
                modifications['force_dependency_refresh'] = True
                logger.info("Will rebuild Docker image with fresh Maven dependencies")
                return modifications
            
            # Check for missing JDK tools
            if 'java.lang.noclassdeffounderror' in error_lower or 'classnotfoundexception' in error_lower:
                logger.info("Detected missing Java class - may need different JDK version")
                # Try to detect required Java version from error
                if 'java 17' in error_lower or 'java/17' in error_lower:
                    modifications['rebuild_docker_image'] = True
                    modifications['java_version_hint'] = '17'
                elif 'java 21' in error_lower or 'java/21' in error_lower:
                    modifications['rebuild_docker_image'] = True
                    modifications['java_version_hint'] = '21'
                else:
                    modifications['rebuild_docker_image'] = True
                logger.info("Will rebuild Docker image with adjusted JDK")
                return modifications
        
        # Check for Gradle issues
        if error_type == "zero_tests_gradle_error":
            if 'could not resolve' in error_lower or 'could not find' in error_lower:
                logger.info("Detected Gradle dependency resolution failure")
                modifications['rebuild_docker_image'] = True
                modifications['force_dependency_refresh'] = True
                return modifications
        
        # Generic: try rebuilding the image with --no-cache
        logger.info("Triggering Docker image rebuild with no cache")
        modifications['rebuild_docker_image'] = True
        modifications['no_cache'] = True
        return modifications
    
    # Attempt 2: Try different healing strategies
    elif attempt == 1:
        logger.info("Healing attempt 2: Trying alternative fixes...")
        
        # For Maven/Gradle: increase memory
        if error_type in ["zero_tests_maven_error", "zero_tests_gradle_error", "zero_tests_dependency_error"]:
            if 'outofmemoryerror' in error_lower or 'gc overhead' in error_lower:
                logger.info("Detected memory issues - increasing heap size")
                modifications['env_vars'] = {
                    'MAVEN_OPTS': '-Xmx4g -XX:+UseG1GC',
                    'GRADLE_OPTS': '-Xmx4g -XX:+UseG1GC',
                }
                modifications['rebuild_docker_image'] = True
                return modifications
        
        # Check for network/proxy issues
        if 'connection' in error_lower or 'timeout' in error_lower or 'proxy' in error_lower:
            logger.info("Detected possible network issues - adding retry logic")
            modifications['timeout_multiplier'] = 2.0
            modifications['rebuild_docker_image'] = True
            return modifications
        
        # Last resort: try a clean rebuild
        logger.info("Trying clean Docker rebuild as last resort")
        modifications['rebuild_docker_image'] = True
        modifications['no_cache'] = True
        modifications['clean_rebuild'] = True
        return modifications
    
    # Attempt 3+: Mark as not healable
    else:
        logger.error("=" * 60)
        logger.error("CANNOT HEAL: All healing attempts exhausted")
        logger.error("=" * 60)
        logger.error(f"Zero tests ran after {attempt + 1} attempts")
        logger.error(f"Error type: {error_type}")
        logger.error("")
        logger.error("This usually means:")
        logger.error("  1. The project has build configuration issues")
        logger.error("  2. Dependencies cannot be resolved")
        logger.error("  3. The test command is incorrect")
        logger.error("  4. The Docker environment is incompatible")
        logger.error("")
        logger.error("Try manually:")
        logger.error("  - Check the test command: is it correct?")
        logger.error("  - Check build logs for specific errors")
        logger.error("  - Try with --test-cmd to override the test command")
        logger.error("=" * 60)
        modifications['not_healable'] = True
        modifications['error_reason'] = f"zero_tests_after_{attempt + 1}_attempts"
        return modifications


# =============================================================================
# Test Execution Healing
# =============================================================================

def apply_test_execution_healing(
    repo_path: Path,
    language: str,
    attempt: int,
    error_type: Optional[str],
    test_result: TestResult,
    logger: logging.Logger
) -> Dict[str, Any]:
    """
    Apply healing strategy for test execution failures.

    Returns modified parameters for next test run attempt.

    Args:
        repo_path: Path to repository
        language: Repository language
        attempt: Attempt number (0-indexed)
        error_type: Type of error detected
        test_result: Previous test result
        logger: Logger instance

    Returns:
        Dict of parameters to modify for next attempt.
        Special key 'rebuild_docker_image' signals that the Docker image needs to be rebuilt.
        Special key 'not_healable' signals the error cannot be fixed with Docker.
    """
    logger.info(f"Applying test execution healing: attempt={attempt+1}, error={error_type}")

    modifications = {}

    # Non-apt dependency errors (like Nix) - cannot be healed with Docker
    if error_type == "requires_nix_package_manager":
        logger.error("=" * 60)
        logger.error("CANNOT HEAL: This project requires the NIX package manager")
        logger.error("=" * 60)
        logger.error("The project depends on Nix C libraries (nix-flake-c, etc.)")
        logger.error("These cannot be installed via apt-get in a standard Docker image.")
        logger.error("")
        logger.error("Options to support this repository:")
        logger.error("  1. Use a NixOS-based Docker image (nixos/nix)")
        logger.error("  2. Add Nix installation to the Dockerfile")
        logger.error("  3. Skip this PR as incompatible with Docker-based testing")
        logger.error("=" * 60)
        modifications['not_healable'] = True
        modifications['error_reason'] = "requires_nix_package_manager"
        return modifications
    
    # Zero tests ran - compilation error (code issues, not environment)
    if error_type == "zero_tests_compilation_error":
        logger.error("=" * 60)
        logger.error("CANNOT HEAL: Compilation error in source code")
        logger.error("=" * 60)
        logger.error("The code failed to compile. This is likely a code issue,")
        logger.error("not an environment issue that can be fixed by healing.")
        logger.error("")
        logger.error("Possible causes:")
        logger.error("  1. The PR is incompatible with the base commit")
        logger.error("  2. Missing dependencies in the repository")
        logger.error("  3. Breaking changes in dependencies")
        logger.error("=" * 60)
        modifications['not_healable'] = True
        modifications['error_reason'] = "compilation_error_in_source"
        return modifications
    
    # Zero tests ran - try to heal based on the specific error
    if is_zero_tests_error(error_type):
        logger.warning("=" * 60)
        logger.warning(f"ZERO TESTS RAN - Attempting healing (attempt {attempt + 1})")
        logger.warning("=" * 60)
        return _heal_zero_tests_error(repo_path, language, attempt, error_type, test_result, logger)

    # Missing system library errors - requires Docker image rebuild with new packages
    if error_type == "missing_system_library":
        logger.info("Healing: Missing system library - signaling Docker image rebuild with new packages")
        error_output = test_result.stdout + test_result.stderr
        healed = _heal_missing_system_library(repo_path, error_output, logger)
        if healed:
            modifications['rebuild_docker_image'] = True
            modifications['added_system_libraries'] = True
            logger.info("✓ Dockerfile updated with missing system libraries")
            logger.info("  Docker image will be rebuilt before next test attempt")
        else:
            # Healing failed - likely a non-apt library
            modifications['not_healable'] = True
            modifications['error_reason'] = "missing_system_library_not_in_apt"
        return modifications

    # Rust edition2024/nightly errors - requires Docker image rebuild
    if language == "rust" and error_type in ["rust_edition2024_error", "rust_unstable_feature_error", "rust_version_mismatch"]:
        logger.info("Healing: Rust requires nightly toolchain - signaling Docker image rebuild")
        # Apply the Dockerfile fix
        healed = _heal_rust_nightly_requirement(repo_path, logger)
        if healed:
            modifications['rebuild_docker_image'] = True
            modifications['rust_switched_to_nightly'] = True
            logger.info("✓ Dockerfile updated to use nightly Rust")
            logger.info("  Docker image will be rebuilt before next test attempt")
        return modifications

    # Timeout errors - increase timeout
    if error_type == "timeout_error":
        logger.info("Healing: Increasing timeout for next attempt")
        modifications['timeout_multiplier'] = 1.5

    # Memory errors - reduce parallelism
    if error_type == "memory_error":
        logger.info("Healing: Reducing test parallelism for next attempt")
        # For Go: add -p 1 flag to run sequentially
        if language == "go":
            modifications['test_flags'] = ['-p', '1']

    # Network errors - add retry environment variables
    if error_type == "network_error":
        logger.info("Healing: Adding network retry environment variables")
        modifications['env_vars'] = {
            'GO_RETRY': '3',
            'GOPROXY': 'https://proxy.golang.org,https://goproxy.io,direct'
        }

    return modifications


# =============================================================================
# Retry Logic Orchestration
# =============================================================================

def should_retry_docker_build(
    attempt: int,
    max_retries: int,
    error_type: Optional[str],
    logger: logging.Logger
) -> bool:
    """
    Determine if Docker build should be retried.

    Args:
        attempt: Current attempt number (0-indexed)
        max_retries: Maximum number of retries
        error_type: Type of error detected
        logger: Logger instance

    Returns:
        True if should retry
    """
    if attempt >= max_retries:
        logger.info(f"Max retries ({max_retries}) reached, not retrying")
        return False

    if not is_retriable_error(error_type):
        logger.info(f"Error type '{error_type}' is not retriable")
        return False

    logger.info(f"Retry {attempt + 1}/{max_retries}: error is retriable")
    return True


def should_retry_test_execution(
    attempt: int,
    max_retries: int,
    test_result: TestResult,
    error_type: Optional[str],
    logger: logging.Logger
) -> bool:
    """
    Determine if test execution should be retried.

    Args:
        attempt: Current attempt number (0-indexed)
        max_retries: Maximum number of retries
        test_result: Test execution result
        error_type: Type of error detected
        logger: Logger instance

    Returns:
        True if should retry
    """
    if attempt >= max_retries:
        logger.info(f"Max retries ({max_retries}) reached, not retrying")
        return False

    # If tests passed, no need to retry
    if test_result.success and test_result.exit_code == 0:
        logger.info("Tests passed, no retry needed")
        return False

    # If it's just test failures (not environment issues), don't retry
    if not error_type:
        logger.info("No environment error detected, just test failures - not retrying")
        return False

    # If error is retriable, retry
    if is_retriable_error(error_type):
        logger.info(f"Retry {attempt + 1}/{max_retries}: error is retriable")
        return True

    logger.info(f"Error type '{error_type}' is not retriable")
    return False


def analyze_test_stability(
    results: List[TestResult],
    logger: logging.Logger
) -> Dict[str, Any]:
    """
    Analyze test results across multiple runs to determine stability.

    Args:
        results: List of test results from multiple attempts
        logger: Logger instance

    Returns:
        Dict with stability analysis
    """
    if not results:
        return {'stable': False, 'reason': 'No results'}

    if len(results) == 1:
        result = results[0]
        has_env_error = detect_test_error_type(result) is not None
        return {
            'stable': not has_env_error,
            'reason': 'Single run - no environment errors' if not has_env_error else 'Environment errors detected',
            'passed': len(result.tests_passed),
            'failed': len(result.tests_failed),
            'skipped': len(result.tests_skipped)
        }

    # Compare last two results
    last = results[-1]
    prev = results[-2]

    # Check if results are consistent
    last_passed = set(last.tests_passed)
    prev_passed = set(prev.tests_passed)
    last_failed = set(last.tests_failed)
    prev_failed = set(prev.tests_failed)

    # Calculate consistency
    passed_consistency = len(last_passed & prev_passed) / max(len(last_passed | prev_passed), 1)
    failed_consistency = len(last_failed & prev_failed) / max(len(last_failed | prev_failed), 1)

    overall_consistency = (passed_consistency + failed_consistency) / 2

    # Check for environment errors
    has_env_error = detect_test_error_type(last) is not None

    stable = overall_consistency > 0.9 and not has_env_error

    logger.info(f"Test stability analysis:")
    logger.info(f"  - Consistency: {overall_consistency:.2%}")
    logger.info(f"  - Environment errors: {'Yes' if has_env_error else 'No'}")
    logger.info(f"  - Stable: {'Yes' if stable else 'No'}")

    return {
        'stable': stable,
        'consistency': overall_consistency,
        'has_env_error': has_env_error,
        'passed': len(last.tests_passed),
        'failed': len(last.tests_failed),
        'skipped': len(last.tests_skipped),
        'reason': f"Consistency: {overall_consistency:.2%}, Env errors: {has_env_error}"
    }
