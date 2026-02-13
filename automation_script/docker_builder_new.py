"""
Docker image building for PR Evaluation Environment.

Generates production-grade Docker images for automated PR evaluation.
Images are self-contained, reproducible, and require no network access during test execution.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from .config import (
    DOCKER_BASE_IMAGE,
    DOCKER_TIMEOUT_BUILD,
    DOCKER_BUILD_JOBS,
    DOCKER_IMAGE_AUTHORS,
    DOCKER_DEFAULT_REPO_URL,
)
from .utils import run_command


# =============================================================================
# Dockerfile Generation Helpers
# =============================================================================

def _dockerfile_header(repo_name: str = "", base_commit: str = "", repo_url: str = "") -> str:
    """Generate minimal Dockerfile header with embedded metadata."""
    lines = ["# syntax=docker/dockerfile:1"]
    if repo_name:
        lines.append(f"# {repo_name} @ {base_commit[:12] if base_commit else 'HEAD'}")
    if repo_url:
        lines.append(f"# {repo_url}")
    lines.append("")
    return "\n".join(lines)


def _base_stage(language: str, repo_url: str = "", base_commit: str = "", repo_name: str = "") -> str:
    """Generate base image setup with system dependencies for harness compatibility."""
    # Use provided repo_url or the configured default for the ARG
    default_repo_url = repo_url if repo_url else DOCKER_DEFAULT_REPO_URL
    
    return f'''FROM {DOCKER_BASE_IMAGE}

ARG REPO_URL={default_repo_url}
ARG BASE_COMMIT
ARG JOBS={DOCKER_BUILD_JOBS}
ARG BUILD_DATE
ARG TARGETARCH

LABEL org.opencontainers.image.title="{repo_name or 'pr-eval'}" \\
      org.opencontainers.image.description="Jaeger project Docker image" \\
      org.opencontainers.image.version="1.0.0" \\
      org.opencontainers.image.created="${{BUILD_DATE}}" \\
      org.opencontainers.image.revision="{base_commit}" \\
      org.opencontainers.image.source="{repo_url}" \\
      org.opencontainers.image.authors="{DOCKER_IMAGE_AUTHORS}"

# Reserve UID 1000 and configure environment
RUN userdel -r ubuntu 2>/dev/null || true
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC

# System dependencies (jq required for harness entry scripts)
RUN apt-get update && apt-get install -y --no-install-recommends \\
    bash ca-certificates curl git wget python3 jq \\
    && rm -rf /var/lib/apt/lists/*

# Directory structure for harness compatibility
# /app/repo      - Primary source location
# /testbed       - Legacy source location (symlink)
# /workspace     - Evaluation workspace
# /swe_util      - Harness utilities and instance data
# /openhands     - OpenHands logs directory
RUN mkdir -p /app/repo /saved/ENV /saved/venv/ENV /workspace \\
    && mkdir -p /swe_util/eval_data/instances /swe_util/eval_data/testbeds \\
    && mkdir -p /openhands/logs && chmod 777 /openhands/logs

# Git configuration for patch operations
RUN git config --global user.name "evaluation" \\
    && git config --global user.email "eval@localhost" \\
    && git config --global --add safe.directory /app/repo \\
    && git config --global --add safe.directory /testbed \\
    && git config --global --add safe.directory /workspace

# Non-root user for security (switch to this user before CMD)
RUN groupadd -r appuser && useradd -r -g appuser -s /sbin/nologin appuser

'''


def _clone_repo() -> str:
    """Clone repository at specified commit with harness-compatible symlinks."""
    return '''# Clone repository
RUN git clone --filter=blob:none "${REPO_URL}" /app/repo \\
    && cd /app/repo && git checkout "${BASE_COMMIT}"

# Create symlinks for harness compatibility
# /testbed -> /app/repo (legacy location expected by some harness scripts)
RUN ln -sf /app/repo /testbed

WORKDIR /app/repo

'''


def _finalize() -> str:
    """Final cleanup and validation for harness compatibility."""
    return '''# Ensure clean git state
RUN cd /app/repo && git checkout -- . 2>/dev/null || true

# Validate build for harness compatibility
RUN set -e \\
    && test -d /app/repo/.git \\
    && test -L /testbed \\
    && test -d /swe_util \\
    && test -d /workspace \\
    && test -d /openhands/logs \\
    && jq --version >/dev/null \\
    && bash --version >/dev/null \\
    && ! getent passwd 1000

# Switch to non-root user for security
USER appuser

CMD ["/bin/bash"]
'''


# =============================================================================
# Language-Specific Generators
# =============================================================================

def generate_python_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for Python projects."""
    logger.info("Generating Python Dockerfile")

    has_requirements = (repo_path / "requirements.txt").exists()
    has_pyproject = (repo_path / "pyproject.toml").exists()
    has_setup = (repo_path / "setup.py").exists()
    has_dev_req = (repo_path / "requirements-dev.txt").exists() or (repo_path / "dev-requirements.txt").exists()
    has_test_req = (repo_path / "requirements-test.txt").exists() or (repo_path / "test-requirements.txt").exists()

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("Python", repo_url, base_commit, repo_name)

    content += '''# Python toolchain
RUN apt-get update && apt-get install -y --no-install-recommends \\
    python3-pip python3-venv python3-dev \\
    build-essential gcc g++ make libffi-dev libssl-dev \\
    && rm -rf /var/lib/apt/lists/*

# Virtual environment
RUN python3 -m venv /saved/venv/ENV
ENV VIRTUAL_ENV=/saved/venv/ENV
ENV PATH="/saved/venv/ENV/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

'''

    content += _clone_repo()

    # Install dependencies
    if has_requirements:
        content += '''# Install requirements
RUN pip install --no-cache-dir -r requirements.txt || true

'''

    if has_dev_req:
        content += '''# Dev requirements
RUN pip install --no-cache-dir -r requirements-dev.txt 2>/dev/null \\
    || pip install --no-cache-dir -r dev-requirements.txt 2>/dev/null || true

'''

    if has_test_req:
        content += '''# Test requirements
RUN pip install --no-cache-dir -r requirements-test.txt 2>/dev/null \\
    || pip install --no-cache-dir -r test-requirements.txt 2>/dev/null || true

'''

    if has_pyproject:
        content += '''# Install package (editable)
RUN pip install --no-cache-dir -e ".[dev,test]" 2>/dev/null \\
    || pip install --no-cache-dir -e ".[test]" 2>/dev/null \\
    || pip install --no-cache-dir -e . || true

'''
    elif has_setup:
        content += '''# Install package (editable)
RUN pip install --no-cache-dir -e . || true

'''

    content += '''# Test framework
RUN pip install --no-cache-dir pytest pytest-json-report pytest-timeout pytest-cov mock || true

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_rust_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_full_name: Optional[str] = None,
    subdir: Optional[str] = None,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for Rust projects."""
    logger.info("Generating Rust Dockerfile")

    # Detect Rust version
    rust_version = "stable"
    for toolchain_file in ["rust-toolchain.toml", "rust-toolchain"]:
        path = repo_path / toolchain_file
        if path.exists():
            try:
                import re
                content = path.read_text()
                match = re.search(r'channel\s*=\s*"([^"]+)"', content)
                if match:
                    rust_version = match.group(1)
                elif toolchain_file == "rust-toolchain":
                    rust_version = content.strip()
                logger.info(f"Detected Rust: {rust_version}")
            except Exception:
                pass
            break

    # Detect system deps from Cargo.lock
    extra_pkgs = []
    cargo_lock = repo_path / "Cargo.lock"
    if cargo_lock.exists():
        try:
            lock_content = cargo_lock.read_text()
            pkg_map = {
                'openssl-sys': 'libssl-dev',
                'libz-sys': 'zlib1g-dev',
                'libgit2-sys': 'libgit2-dev',
                'cmake': 'cmake',
                'bindgen': 'clang libclang-dev',
            }
            for crate, pkgs in pkg_map.items():
                if f'name = "{crate}"' in lock_content:
                    extra_pkgs.extend(pkgs.split())
        except Exception:
            pass

    workdir = f"/app/repo/{subdir}" if subdir else "/app/repo"
    effective_name = repo_name or repo_full_name or ""

    content = _dockerfile_header(effective_name, base_commit, repo_url)
    content += _base_stage("Rust", repo_url, base_commit, effective_name)

    pkg_list = ["build-essential", "pkg-config", "libssl-dev"] + list(set(extra_pkgs))
    content += f'''# Rust build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {" ".join(pkg_list)} \\
    && rm -rf /var/lib/apt/lists/*

# Rust toolchain (multi-arch: amd64 + arm64)
# rustup automatically detects architecture and installs correct toolchain
ENV RUSTUP_HOME=/saved/ENV/rustup
ENV CARGO_HOME=/saved/ENV/cargo
ENV PATH="/saved/ENV/cargo/bin:$PATH"
ENV CARGO_BUILD_JOBS=$JOBS

# Validate architecture before installing Rust toolchain
RUN ARCH=$(uname -m) && \\
    case "$ARCH" in \\
        x86_64|aarch64) ;; \\
        *) echo "Unsupported arch: $ARCH — only amd64 and arm64 are supported" && exit 1 ;; \\
    esac && \\
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \\
    sh -s -- -y --default-toolchain {rust_version} --profile minimal \\
    && rustup component add rustfmt clippy || true

'''

    content += _clone_repo()

    if subdir:
        content += f'''WORKDIR {workdir}

'''

    content += '''# Cache dependencies
RUN cargo fetch --locked 2>/dev/null || cargo fetch || true
RUN cargo build --release 2>/dev/null || cargo build || true

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_go_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for Go projects."""
    logger.info("Generating Go Dockerfile")

    # Detect Go version
    STABLE_VERSIONS = {
        "1.24": "1.24.0", "1.23": "1.23.6", "1.22": "1.22.12",
        "1.21": "1.21.13", "1.20": "1.20.14",
    }
    go_version = "1.23.6"

    go_mod = repo_path / "go.mod"
    if go_mod.exists():
        try:
            for line in go_mod.read_text().splitlines():
                if line.strip().startswith("go "):
                    ver = line.strip().split()[1]
                    parts = ver.split(".")
                    if len(parts) >= 3:
                        go_version = ver
                    elif len(parts) == 2:
                        minor = f"{parts[0]}.{parts[1]}"
                        if minor in STABLE_VERSIONS:
                            go_version = STABLE_VERSIONS[minor]
                        else:
                            # For Go < 1.21, initial release is goX.Y (no .0)
                            # For Go >= 1.21, initial release is goX.Y.0
                            try:
                                major_ver = int(parts[0])
                                minor_ver = int(parts[1])
                                if major_ver == 1 and minor_ver < 21:
                                    go_version = minor
                                else:
                                    go_version = f"{minor}.0"
                            except ValueError:
                                go_version = f"{minor}.0"
                    logger.info(f"Detected Go: {go_version}")
                    break
        except Exception:
            pass

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("Go", repo_url, base_commit, repo_name)

    content += f'''# Go build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential gcc g++ \\
    && rm -rf /var/lib/apt/lists/*

# Go toolchain (multi-arch: amd64 + arm64)
ENV GOPATH=/saved/ENV
ENV GOMODCACHE=/saved/ENV/pkg/mod
ENV PATH="/usr/local/go/bin:/saved/ENV/bin:$PATH"

RUN ARCH=$(uname -m) && \\
    case "$ARCH" in \\
        x86_64)  GOARCH=amd64 ;; \\
        aarch64) GOARCH=arm64 ;; \\
        *)       echo "Unsupported arch: $ARCH — only amd64 and arm64 are supported" && exit 1 ;; \\
    esac && \\
    curl -fsSL "https://go.dev/dl/go{go_version}.linux-$GOARCH.tar.gz" | tar -C /usr/local -xz

'''

    content += _clone_repo()

    content += '''# Cache dependencies
RUN go mod download || true
RUN go build -v ./... 2>/dev/null || true

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_node_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for Node.js projects."""
    logger.info("Generating Node.js Dockerfile")

    node_version = "20"
    use_pnpm = (repo_path / "pnpm-lock.yaml").exists()
    use_yarn = (repo_path / "yarn.lock").exists()

    # Detect Node version - check .nvmrc first (most reliable for exact version)
    nvmrc = repo_path / ".nvmrc"
    if nvmrc.exists():
        try:
            import re
            nvmrc_content = nvmrc.read_text().strip()
            # Handle formats: "8", "v8", "8.12.0", "lts/carbon", etc.
            # Map LTS names to versions
            lts_map = {
                "argon": "4", "boron": "6", "carbon": "8", "dubnium": "10",
                "erbium": "12", "fermium": "14", "gallium": "16", "hydrogen": "18",
                "iron": "20", "jod": "22"
            }
            lts_match = re.search(r'lts/(\w+)', nvmrc_content.lower())
            if lts_match and lts_match.group(1) in lts_map:
                node_version = lts_map[lts_match.group(1)]
                logger.info(f"Detected Node {node_version} from .nvmrc (LTS: {lts_match.group(1)})")
            else:
                # Extract major version number
                version_match = re.search(r'v?(\d+)', nvmrc_content)
                if version_match:
                    node_version = version_match.group(1)
                    logger.info(f"Detected Node {node_version} from .nvmrc")
        except Exception as e:
            logger.debug(f"Error reading .nvmrc: {e}")

    # If no .nvmrc, check package.json engines
    if node_version == "20":  # Still default, try package.json
        pkg_json = repo_path / "package.json"
        if pkg_json.exists():
            try:
                import json
                import re
                pkg = json.loads(pkg_json.read_text())
                node_req = pkg.get("engines", {}).get("node", "")
                if node_req:
                    # Handle version ranges like ">=8.12.0", "^14.0.0", ">=8 <12"
                    # Extract the first version number as the minimum required
                    match = re.search(r'(\d+)', node_req)
                    if match:
                        detected_version = int(match.group(1))
                        # Check for upper bounds like "<=11" or "<12"
                        upper_match = re.search(r'[<]=?\s*(\d+)', node_req)
                        if upper_match:
                            upper_bound = int(upper_match.group(1))
                            # Use a version that satisfies the range
                            if detected_version < upper_bound:
                                node_version = str(detected_version)
                            else:
                                node_version = str(upper_bound - 1) if upper_bound > detected_version else str(detected_version)
                        else:
                            node_version = str(detected_version)
                        logger.info(f"Detected Node {node_version} from package.json engines: {node_req}")
                if "pnpm" in pkg.get("packageManager", "").lower():
                    use_pnpm = True
            except Exception:
                pass

    # Ensure we use a valid Node.js version available from NodeSource
    # NodeSource provides: 18, 20, 22 for current; 14, 16 for older
    # For very old versions (8, 10, 12), we use nvm instead
    old_node = int(node_version) < 14
    logger.info(f"Using Node.js version: {node_version} (old_node={old_node})")

    # Detect if Java is needed (e.g., for Google Closure Compiler)
    needs_java = False
    needs_chrome = False
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            pkg_text = pkg_json.read_text().lower()
            # Check for Google Closure Compiler or other Java-dependent tools
            if any(dep in pkg_text for dep in [
                "closure-compiler", "google-closure", "grunt-closure",
                "webpack-closure-compiler", "google-closure-compiler"
            ]):
                needs_java = True
                logger.info("Detected Google Closure Compiler - Java will be installed")
            # Check for Karma or other browser-based test runners
            if any(dep in pkg_text for dep in [
                "karma", "puppeteer", "playwright", "selenium", "webdriver",
                "chrome-launcher", "chromium"
            ]):
                needs_chrome = True
                logger.info("Detected browser-based testing (Karma/Puppeteer) - Chrome will be installed")
        except Exception:
            pass
    # Also check Gruntfile for closure
    gruntfile = repo_path / "Gruntfile.js"
    if gruntfile.exists():
        try:
            grunt_text = gruntfile.read_text().lower()
            if "closure" in grunt_text or "minall" in grunt_text:
                needs_java = True
                logger.info("Detected closure/minall in Gruntfile - Java will be installed")
            if "karma" in grunt_text:
                needs_chrome = True
                logger.info("Detected Karma in Gruntfile - Chrome will be installed")
        except Exception:
            pass
    # Check karma.conf.js for browser requirements
    karma_conf = repo_path / "karma.conf.js"
    if karma_conf.exists() or (repo_path / "karma-shared.conf.js").exists():
        needs_chrome = True
        logger.info("Detected karma.conf.js - Chrome will be installed")

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("JavaScript", repo_url, base_commit, repo_name)

    # Build tools for native modules (needed before Node install)
    extra_deps = "python3 build-essential"
    if needs_java:
        extra_deps += " default-jre-headless"

    content += f'''# Build tools for native modules
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {extra_deps} \\
    && rm -rf /var/lib/apt/lists/*

'''

    # Install Chromium for Karma/Puppeteer/Playwright tests
    if needs_chrome:
        content += '''# Chromium for browser-based testing (Karma, Puppeteer, etc.)
# Use the Chromium from the Playwright team's repository (works without snap)
RUN apt-get update && apt-get install -y --no-install-recommends \\
    fonts-liberation libasound2t64 libatk-bridge2.0-0 \\
    libatk1.0-0 libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 \\
    libnspr4 libnss3 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 \\
    libxrandr2 xdg-utils libx11-xcb1 libxcb-dri3-0 libxshmfence1 \\
    && rm -rf /var/lib/apt/lists/*

# Download and install Chrome for Testing (multi-arch: amd64 + arm64)
RUN apt-get update && apt-get install -y --no-install-recommends unzip \\
    && rm -rf /var/lib/apt/lists/* \\
    && ARCH=$(uname -m) \\
    && case "$ARCH" in \\
        x86_64)  CHROME_ARCH="linux64"; CHROME_DIR="chrome-linux64" ;; \\
        aarch64) CHROME_ARCH="linux-arm64"; CHROME_DIR="chrome-linux-arm64" ;; \\
        *)       echo "Unsupported arch: $ARCH — only amd64 and arm64 are supported" && exit 1 ;; \\
    esac \\
    && CHROME_VERSION=$(curl -s https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_STABLE) \\
    && curl -fsSL "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/${CHROME_ARCH}/${CHROME_DIR}.zip" -o /tmp/chrome.zip \\
    && unzip /tmp/chrome.zip -d /opt \\
    && ln -sf /opt/${CHROME_DIR}/chrome /usr/local/bin/chromium \\
    && ln -sf /opt/${CHROME_DIR}/chrome /usr/local/bin/chrome \\
    && rm /tmp/chrome.zip

# Set Chrome environment variables for Karma
ENV CHROME_BIN=/usr/local/bin/chromium
ENV CHROMIUM_BIN=/usr/local/bin/chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/chromium

'''

    if old_node:
        # Use n (node version manager) for old Node versions (8, 10, 12) that aren't in NodeSource
        # First install a modern Node via NodeSource to get npm, then use n to install the old version
        content += f'''# Node.js toolchain via n (for older versions)
# First install modern Node to get npm, then use n to install old version
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \\
    && apt-get install -y nodejs \\
    && rm -rf /var/lib/apt/lists/*

# Use n to install the required old Node version
RUN npm install -g n \\
    && n {node_version} \\
    && hash -r

# Verify correct Node version
RUN node --version && npm --version

'''
    else:
        # Use NodeSource for newer versions (14+)
        content += f'''# Node.js toolchain
RUN curl -fsSL https://deb.nodesource.com/setup_{node_version}.x | bash - \\
    && apt-get install -y nodejs \\
    && rm -rf /var/lib/apt/lists/*

'''

    if use_pnpm:
        content += '''RUN npm install -g pnpm

'''
    elif use_yarn:
        content += '''RUN npm install -g yarn

'''

    content += '''ENV NODE_PATH=/app/repo/node_modules
ENV PATH="/app/repo/node_modules/.bin:$PATH"

'''

    content += _clone_repo()

    if use_pnpm:
        content += '''# Install dependencies
RUN pnpm install --frozen-lockfile 2>/dev/null || pnpm install || true

'''
    elif use_yarn:
        # Add --ignore-engines for old Node versions that may have engine mismatches
        if old_node:
            content += '''# Install dependencies (--ignore-engines for old Node compatibility)
RUN yarn install --frozen-lockfile --ignore-engines 2>/dev/null || yarn install --ignore-engines || true

'''
        else:
            content += '''# Install dependencies
RUN yarn install --frozen-lockfile 2>/dev/null || yarn install || true

'''
    else:
        content += '''# Install dependencies
RUN npm ci --ignore-scripts 2>/dev/null || npm install || true

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def _detect_java_version(repo_path: Path, logger: logging.Logger) -> int:
    """
    Detect required Java version from Gradle or Maven build files.
    
    Checks:
    - build.gradle.kts: sourceCompatibility, targetCompatibility, toolchain
    - build.gradle: sourceCompatibility, targetCompatibility, toolchain
    - pom.xml: maven.compiler.source, maven.compiler.target, java.version
    
    Returns:
        Java version number (e.g., 17, 21). Defaults to 17.
    """
    import re
    
    java_version = 17  # Default
    
    # Check Gradle Kotlin DSL
    gradle_kts = repo_path / "build.gradle.kts"
    if gradle_kts.exists():
        try:
            content = gradle_kts.read_text()
            
            # Check for toolchain { languageVersion.set(JavaLanguageVersion.of(21)) }
            toolchain_match = re.search(r'languageVersion\.set\s*\(\s*JavaLanguageVersion\.of\s*\(\s*(\d+)\s*\)\s*\)', content)
            if toolchain_match:
                java_version = int(toolchain_match.group(1))
                logger.info(f"Detected Java {java_version} from Gradle toolchain")
                return java_version
            
            # Check for jvmToolchain(21)
            jvm_toolchain_match = re.search(r'jvmToolchain\s*\(\s*(\d+)\s*\)', content)
            if jvm_toolchain_match:
                java_version = int(jvm_toolchain_match.group(1))
                logger.info(f"Detected Java {java_version} from jvmToolchain")
                return java_version
            
            # Check for sourceCompatibility = JavaVersion.VERSION_21 or "21"
            compat_match = re.search(r'(?:source|target)Compatibility\s*=\s*(?:JavaVersion\.VERSION_)?["\']?(\d+)["\']?', content)
            if compat_match:
                java_version = int(compat_match.group(1))
                logger.info(f"Detected Java {java_version} from Gradle compatibility setting")
                return java_version
                
        except Exception as e:
            logger.debug(f"Error reading build.gradle.kts: {e}")
    
    # Check Gradle Groovy DSL
    gradle_groovy = repo_path / "build.gradle"
    if gradle_groovy.exists():
        try:
            content = gradle_groovy.read_text()
            
            # Check for toolchain { languageVersion = JavaLanguageVersion.of(21) }
            toolchain_match = re.search(r'languageVersion\s*=\s*JavaLanguageVersion\.of\s*\(\s*(\d+)\s*\)', content)
            if toolchain_match:
                java_version = int(toolchain_match.group(1))
                logger.info(f"Detected Java {java_version} from Gradle toolchain")
                return java_version
            
            # Check for sourceCompatibility = '21' or = 21
            compat_match = re.search(r'(?:source|target)Compatibility\s*=\s*["\']?(\d+)["\']?', content)
            if compat_match:
                java_version = int(compat_match.group(1))
                logger.info(f"Detected Java {java_version} from Gradle compatibility setting")
                return java_version
                
        except Exception as e:
            logger.debug(f"Error reading build.gradle: {e}")
    
    # Check Maven pom.xml files (root and subdirectories)
    pom_files = [repo_path / "pom.xml"]
    # Also check common submodule locations
    for subdir in repo_path.iterdir():
        if subdir.is_dir():
            sub_pom = subdir / "pom.xml"
            if sub_pom.exists():
                pom_files.append(sub_pom)

    max_java_version = 17  # Track highest version found
    for pom_xml in pom_files:
        if pom_xml.exists():
            try:
                content = pom_xml.read_text()

                # Check for various Java version properties:
                # <java.version>21</java.version>
                # <maven.compiler.source>21</maven.compiler.source>
                # <maven.compiler.target>21</maven.compiler.target>
                # <maven.compiler.release>21</maven.compiler.release>
                # <minimum.java.version>21</minimum.java.version>
                # <testRelease>21</testRelease>
                version_patterns = [
                    r'<java\.version>\s*(\d+)\s*</',
                    r'<maven\.compiler\.(?:source|target|release)>\s*(\d+)\s*</',
                    r'<minimum\.java\.version>\s*(\d+)\s*</',
                    r'<testRelease>\s*\$\{minimum\.java\.version\}',  # Reference pattern
                    r'<testRelease>\s*(\d+)\s*</',
                ]

                for pattern in version_patterns:
                    version_match = re.search(pattern, content)
                    if version_match and version_match.lastindex:
                        found_version = int(version_match.group(1))
                        if found_version > max_java_version:
                            max_java_version = found_version
                            logger.info(f"Detected Java {found_version} from {pom_xml.name}")
                    elif version_match and 'minimum.java.version' in pattern:
                        # Found reference to minimum.java.version, look for its definition
                        min_ver_match = re.search(r'<minimum\.java\.version>\s*(\d+)\s*</', content)
                        if min_ver_match:
                            found_version = int(min_ver_match.group(1))
                            if found_version > max_java_version:
                                max_java_version = found_version
                                logger.info(f"Detected Java {found_version} from minimum.java.version in {pom_xml.name}")

            except Exception as e:
                logger.debug(f"Error reading {pom_xml}: {e}")

    if max_java_version > 17:
        return max_java_version
    
    logger.info(f"Using default Java version: {java_version}")
    return java_version


def generate_java_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for Java projects."""
    logger.info("Generating Java Dockerfile")

    use_gradle = (repo_path / "build.gradle").exists() or (repo_path / "build.gradle.kts").exists()
    has_gradlew = (repo_path / "gradlew").exists()
    
    # Detect required Java version
    java_version = _detect_java_version(repo_path, logger)
    
    # Map Java version to Ubuntu package name
    # Ubuntu 24.04 has: openjdk-8, openjdk-11, openjdk-17, openjdk-21
    if java_version >= 21:
        jdk_package = "openjdk-21-jdk-headless"
        java_version_num = "21"
    elif java_version >= 17:
        jdk_package = "openjdk-17-jdk-headless"
        java_version_num = "17"
    elif java_version >= 11:
        jdk_package = "openjdk-11-jdk-headless"
        java_version_num = "11"
    else:
        jdk_package = "openjdk-8-jdk-headless"
        java_version_num = "8"
    
    logger.info(f"Using JDK package: {jdk_package}")

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("Java", repo_url, base_commit, repo_name)

    content += f'''# Java toolchain (Java {java_version}, multi-arch: amd64 + arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {jdk_package} maven \\
    && rm -rf /var/lib/apt/lists/*

# Set JAVA_HOME dynamically based on architecture and create symlink for consistent path
RUN ARCH=$(dpkg --print-architecture) && \\
    case "$ARCH" in \\
        amd64|arm64) ;; \\
        *) echo "Unsupported arch: $ARCH — only amd64 and arm64 are supported" && exit 1 ;; \\
    esac && \\
    ln -sf /usr/lib/jvm/java-{java_version_num}-openjdk-$ARCH /usr/lib/jvm/java-{java_version_num}-openjdk

ENV JAVA_HOME=/usr/lib/jvm/java-{java_version_num}-openjdk
ENV MAVEN_OPTS="-Dmaven.repo.local=/saved/ENV/m2/repository"
ENV PATH="$JAVA_HOME/bin:$PATH"

'''

    content += _clone_repo()

    if use_gradle:
        if has_gradlew:
            content += '''# Cache dependencies
RUN chmod +x ./gradlew 2>/dev/null || true
RUN ./gradlew dependencies --no-daemon 2>/dev/null || true
RUN ./gradlew assemble -x test --no-daemon 2>/dev/null || true

'''
        else:
            content += '''# Cache dependencies
RUN gradle dependencies 2>/dev/null || true
RUN gradle assemble -x test 2>/dev/null || true

'''
    else:
        content += '''# Cache dependencies
RUN mvn dependency:go-offline -B 2>/dev/null || true
RUN mvn clean install -DskipTests -Dmaven.javadoc.skip=true \\
    -Dcheckstyle.skip=true -Dspotbugs.skip=true -Dpmd.skip=true \\
    -Dfindbugs.skip=true -Drat.skip=true -Denforcer.skip=true \\
    -Dlicense.skip=true -T $JOBS -B 2>/dev/null || true

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_csharp_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    subdir: Optional[str] = None,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for .NET/C# projects."""
    logger.info("Generating .NET Dockerfile")

    workdir = f"/app/repo/{subdir}" if subdir else "/app/repo"

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("C#", repo_url, base_commit, repo_name)

    content += '''# .NET SDK
RUN apt-get update && apt-get install -y --no-install-recommends apt-transport-https \\
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb -o /tmp/ms.deb \\
    && dpkg -i /tmp/ms.deb && rm /tmp/ms.deb

RUN apt-get update && apt-get install -y --no-install-recommends dotnet-sdk-8.0 \\
    && rm -rf /var/lib/apt/lists/*

'''

    content += _clone_repo()

    if subdir:
        content += f'''WORKDIR {workdir}

'''

    content += '''# Remove global.json to use available SDK
RUN rm -f global.json 2>/dev/null || true

# Cache dependencies
RUN dotnet restore 2>/dev/null || true
RUN dotnet build --no-restore -c Release 2>/dev/null || dotnet build --no-restore 2>/dev/null || true

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_ruby_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for Ruby projects."""
    logger.info("Generating Ruby Dockerfile")

    extra_pkgs = []
    gemfile = repo_path / "Gemfile"
    if gemfile.exists():
        try:
            content = gemfile.read_text()
            if 'pg' in content or 'postgresql' in content.lower():
                extra_pkgs.append('libpq-dev')
            if 'mysql2' in content:
                extra_pkgs.append('default-libmysqlclient-dev')
            if 'sqlite3' in content:
                extra_pkgs.append('libsqlite3-dev')
        except Exception:
            pass

    pkg_list = ["ruby-full", "ruby-dev", "build-essential", "cmake",
                "libffi-dev", "libssl-dev", "libyaml-dev", "zlib1g-dev"] + extra_pkgs

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("Ruby", repo_url, base_commit, repo_name)

    content += f'''# Ruby toolchain
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {" ".join(pkg_list)} \\
    && rm -rf /var/lib/apt/lists/*

RUN gem install bundler -v '~> 2.0' --no-document

'''

    content += _clone_repo()

    content += '''# Cache dependencies
RUN bundle config set --local without '' \\
    && bundle install --jobs=$JOBS --retry=3 || bundle install --retry=3

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_nix_rust_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    subdir: Optional[str] = None,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for Rust projects requiring Nix."""
    logger.info("Generating NixOS-based Rust Dockerfile")

    workdir = f"/app/repo/{subdir}" if subdir else "/app/repo"
    default_repo_url = repo_url if repo_url else DOCKER_DEFAULT_REPO_URL

    content = f'''# syntax=docker/dockerfile:1
# {repo_name} @ {base_commit[:12] if base_commit else "HEAD"}

FROM nixos/nix:latest

ARG REPO_URL={default_repo_url}
ARG BASE_COMMIT
ARG JOBS={DOCKER_BUILD_JOBS}
ARG BUILD_DATE

LABEL org.opencontainers.image.title="{repo_name or 'pr-eval'}" \\
      org.opencontainers.image.description="Jaeger project Docker image (NixOS)" \\
      org.opencontainers.image.version="1.0.0" \\
      org.opencontainers.image.created="${{BUILD_DATE}}" \\
      org.opencontainers.image.revision="{base_commit}" \\
      org.opencontainers.image.source="{repo_url}" \\
      org.opencontainers.image.authors="{DOCKER_IMAGE_AUTHORS}"

RUN mkdir -p /etc/nix && echo "experimental-features = nix-command flakes" >> /etc/nix/nix.conf
RUN mkdir -p /app/repo /saved/ENV /workspace

RUN nix-channel --update
RUN nix profile install nixpkgs#rustup nixpkgs#pkg-config nixpkgs#python3 nixpkgs#gcc nixpkgs#openssl || true

RUN rustup default stable && rustup component add rustfmt clippy || true

RUN git config --global user.name "evaluation" \\
    && git config --global user.email "eval@localhost" \\
    && git config --global --add safe.directory /app/repo

RUN git clone --filter=blob:none "${{REPO_URL}}" /app/repo \\
    && cd /app/repo && git checkout "${{BASE_COMMIT}}"

WORKDIR {workdir}

RUN cargo fetch --locked 2>/dev/null || cargo fetch || true
RUN cargo build -j $JOBS 2>/dev/null || cargo build || true

RUN cd /app/repo && git checkout -- . 2>/dev/null || true

CMD ["/bin/bash"]
'''

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_php_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """Generate Dockerfile for PHP projects."""
    logger.info("Generating PHP Dockerfile")

    # Detect PHP subdirectory (common in monorepos like OpnForm with api/ folder)
    php_subdir = None
    composer_path = repo_path / "composer.json"
    
    if not composer_path.exists():
        # Check common subdirectories for PHP projects
        for subdir in ["api", "backend", "app", "src", "php"]:
            subdir_path = repo_path / subdir
            if (subdir_path / "composer.json").exists():
                php_subdir = subdir
                composer_path = subdir_path / "composer.json"
                logger.info(f"Detected PHP project in subdirectory: {subdir}")
                break
        
        # Also check for any directory with composer.json
        if not php_subdir:
            for composer_file in repo_path.glob("*/composer.json"):
                php_subdir = composer_file.parent.name
                composer_path = composer_file
                logger.info(f"Found composer.json in subdirectory: {php_subdir}")
                break

    has_composer = composer_path.exists()
    php_root = repo_path / php_subdir if php_subdir else repo_path
    has_phpunit_xml = (php_root / "phpunit.xml").exists() or (php_root / "phpunit.xml.dist").exists()

    # Detect PHP version from composer.json
    php_version = "8.2"  # Default to PHP 8.2
    requires_xdebug = False
    
    # Minimum PHP version available in ondrej/php PPA
    # PHP 5.x and 6.x are not available; oldest available is 7.0
    MIN_SUPPORTED_PHP = "7.4"  # Use 7.4 as safe minimum (widely compatible)
    SUPPORTED_PHP_VERSIONS = ["7.0", "7.1", "7.2", "7.3", "7.4", "8.0", "8.1", "8.2", "8.3"]
    
    if has_composer:
        try:
            import json
            with open(composer_path) as f:
                composer_data = json.load(f)
                require = composer_data.get("require", {})
                require_dev = composer_data.get("require-dev", {})
                php_constraint = require.get("php", "")
                # Parse PHP version constraint (e.g., "^8.1", ">=8.0", "~8.1")
                import re
                match = re.search(r'(\d+\.\d+)', php_constraint)
                if match:
                    detected_version = match.group(1)
                    major_minor = detected_version.split(".")
                    major = int(major_minor[0])
                    
                    # Handle >= constraints: use default (8.2) if minimum is below supported range
                    # For ^/~ constraints: use specified version if supported, else use compatible version
                    is_minimum_constraint = php_constraint.strip().startswith(">=")
                    
                    if major < 7:
                        # PHP 5.x/6.x not available in PPA
                        # Use PHP 7.4 for old projects - it's the last version before major breaking changes
                        # (each() removed in 8.0, many old PHPUnit versions incompatible with 8.x)
                        php_version = "7.4"
                        logger.info(f"PHP constraint {php_constraint} specifies old version; using PHP {php_version} for compatibility")
                    elif detected_version in SUPPORTED_PHP_VERSIONS:
                        php_version = detected_version
                        logger.info(f"Detected PHP version constraint: {php_constraint} -> using {php_version}")
                    else:
                        # Version not in our list (maybe too new), use default
                        php_version = "8.2"
                        logger.info(f"PHP constraint {php_constraint} -> version {detected_version} not in supported list, using {php_version}")
                        
                # Check if xdebug is required
                if "ext-xdebug" in require or "ext-xdebug" in require_dev:
                    requires_xdebug = True
                    logger.info("Project requires xdebug extension")
        except Exception as e:
            logger.warning(f"Could not parse composer.json for PHP version: {e}")

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("PHP", repo_url, base_commit, repo_name)

    # Determine the vendor/bin path based on subdirectory
    vendor_path = f"/app/repo/{php_subdir}/vendor/bin" if php_subdir else "/app/repo/vendor/bin"
    
    # Use ondrej/php PPA for specific PHP versions
    content += f'''# PHP toolchain (using ondrej/php PPA for version control)
RUN apt-get update && apt-get install -y --no-install-recommends \\
    software-properties-common gnupg2 \\
    && add-apt-repository -y ppa:ondrej/php \\
    && apt-get update \\
    && apt-get install -y --no-install-recommends \\
        php{php_version}-cli php{php_version}-common php{php_version}-curl \\
        php{php_version}-mbstring php{php_version}-xml php{php_version}-zip \\
        php{php_version}-intl php{php_version}-bcmath php{php_version}-gd \\
        php{php_version}-mysql php{php_version}-pgsql php{php_version}-sqlite3 \\
        php{php_version}-xdebug php{php_version}-opcache \\
        unzip \\
    && rm -rf /var/lib/apt/lists/*

# Install Composer
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
ENV COMPOSER_ALLOW_SUPERUSER=1
ENV PATH="{vendor_path}:$PATH"

'''

    content += _clone_repo()

    # Change to subdirectory if PHP is not at root
    if php_subdir:
        content += f'''WORKDIR /app/repo/{php_subdir}

'''

    # Install dependencies with fallbacks for platform requirements
    if has_composer:
        content += '''# Install PHP dependencies
# Use --no-security-blocking for old packages with security advisories
RUN composer install --no-interaction --prefer-dist --no-progress --no-security-blocking 2>/dev/null \\
    || composer install --no-interaction --prefer-dist --no-progress --no-security-blocking --ignore-platform-reqs

'''

    # Reset workdir to repo root for consistency with harness
    if php_subdir:
        content += '''WORKDIR /app/repo

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


def generate_c_dockerfile(
    repo_path: Path,
    logger: logging.Logger,
    repo_name: str = "",
    base_commit: str = "",
    repo_url: str = ""
) -> Path:
    """
    Generate Dockerfile for C/autoconf projects (like ruby/ruby, CPython, etc.).

    Supports projects using:
    - GNU Autoconf (configure.ac + autogen.sh)
    - CMake (CMakeLists.txt)
    - Plain Makefile
    """
    logger.info("Generating C/Autoconf Dockerfile")

    has_configure_ac = (repo_path / "configure.ac").exists()
    has_autogen = (repo_path / "autogen.sh").exists()
    has_configure = (repo_path / "configure").exists()
    has_cmake = (repo_path / "CMakeLists.txt").exists()
    has_makefile = (repo_path / "Makefile").exists() or (repo_path / "GNUmakefile").exists()

    # Detect if this is ruby/ruby specifically (needs baseruby)
    is_ruby_interpreter = repo_name and "ruby/ruby" in repo_name.lower()

    # Detect if project has Rust components (like ruby/ruby's YJIT)
    has_rust = (repo_path / "Cargo.toml").exists()

    content = _dockerfile_header(repo_name, base_commit, repo_url)
    content += _base_stage("C", repo_url, base_commit, repo_name)

    # Base C/C++ build tools
    build_packages = [
        "build-essential", "gcc", "g++", "make", "autoconf", "automake",
        "libtool", "pkg-config", "bison", "flex", "gperf",
        # Common libraries
        "libssl-dev", "libreadline-dev", "zlib1g-dev", "libyaml-dev",
        "libffi-dev", "libgmp-dev", "libncurses-dev",
    ]

    # Add CMake if needed
    if has_cmake:
        build_packages.append("cmake")

    # Add Ruby if this is the Ruby interpreter (needs baseruby for bootstrap)
    if is_ruby_interpreter:
        build_packages.append("ruby")  # System Ruby as baseruby

    # Add Rust if project has Rust components
    if has_rust:
        build_packages.extend(["rustc", "cargo"])

    content += f'''# C/C++ build toolchain
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {" ".join(build_packages)} \\
    && rm -rf /var/lib/apt/lists/*

'''

    content += _clone_repo()

    # Build steps based on detected build system
    if has_configure_ac and has_autogen:
        # GNU Autoconf with autogen.sh
        content += '''# Generate configure script and build
RUN ./autogen.sh || true
RUN mkdir -p build && cd build && \\
    ../configure --disable-install-doc && \\
    make -j$JOBS || make

'''
    elif has_configure_ac and not has_autogen:
        # GNU Autoconf without autogen.sh (need autoreconf)
        content += '''# Generate configure script and build
RUN autoreconf -fiv || true
RUN mkdir -p build && cd build && \\
    ../configure && \\
    make -j$JOBS || make

'''
    elif has_configure:
        # Pre-generated configure script
        content += '''# Build with configure
RUN mkdir -p build && cd build && \\
    ../configure && \\
    make -j$JOBS || make

'''
    elif has_cmake:
        # CMake project
        content += '''# Build with CMake
RUN mkdir -p build && cd build && \\
    cmake .. -DCMAKE_BUILD_TYPE=Release && \\
    make -j$JOBS || make

'''
    elif has_makefile:
        # Plain Makefile
        content += '''# Build with Make
RUN make -j$JOBS || make

'''
    else:
        # Fallback - try common patterns
        content += '''# Attempt build (unknown build system)
RUN if [ -f autogen.sh ]; then ./autogen.sh; fi
RUN if [ -f configure ]; then ./configure && make -j$JOBS; \\
    elif [ -f Makefile ]; then make -j$JOBS; \\
    elif [ -f CMakeLists.txt ]; then mkdir -p build && cd build && cmake .. && make -j$JOBS; fi

'''

    content += _finalize()

    dockerfile_path = repo_path / "Dockerfile.pr-eval"
    dockerfile_path.write_text(content)
    logger.info(f"Generated: {dockerfile_path}")
    return dockerfile_path


# =============================================================================
# Main Entry Points
# =============================================================================

def detect_nix_requirements(repo_path: Path, logger: logging.Logger) -> bool:
    """Detect if a Rust project requires the Nix package manager."""
    if not (repo_path / "flake.nix").exists():
        return False

    cargo_lock = repo_path / "Cargo.lock"
    if cargo_lock.exists():
        try:
            content = cargo_lock.read_text()
            nix_crates = ['nix-bindings', 'nix-bindings-bindgen-raw', 'nix-bindings-sys']
            for crate in nix_crates:
                if f'name = "{crate}"' in content:
                    logger.info(f"Detected '{crate}' - requires Nix")
                    return True
        except Exception:
            pass
    return False


def generate_dockerfile(
    repo_path: Path,
    language: str,
    logger: logging.Logger,
    repo_full_name: Optional[str] = None,
    base_commit: Optional[str] = None,
    repo_url: Optional[str] = None
) -> Path:
    """
    Generate Dockerfile based on detected language.

    Args:
        repo_path: Path to repository
        language: Detected language
        logger: Logger instance
        repo_full_name: Repository name (e.g., "owner/repo")
        base_commit: Base commit SHA
        repo_url: Repository URL

    Returns:
        Path to generated Dockerfile
    """
    logger.info(f"Generating Dockerfile for {language}")
    if repo_full_name:
        logger.info(f"  Repository: {repo_full_name}")
    if base_commit:
        logger.info(f"  Commit: {base_commit[:12]}")

    kwargs = {
        "repo_name": repo_full_name or "",
        "base_commit": base_commit or "",
        "repo_url": repo_url or ""
    }

    if language == "python":
        return generate_python_dockerfile(repo_path, logger, **kwargs)

    elif language in ("javascript", "typescript"):
        return generate_node_dockerfile(repo_path, logger, **kwargs)

    elif language == "go":
        return generate_go_dockerfile(repo_path, logger, **kwargs)

    elif language == "java":
        return generate_java_dockerfile(repo_path, logger, **kwargs)

    elif language == "rust":
        # Check for subdirectory
        rust_subdir = None
        if not (repo_path / "Cargo.toml").exists():
            cargo_files = list(repo_path.glob("*/Cargo.toml"))
            if cargo_files:
                rust_subdir = cargo_files[0].parent.name
                logger.info(f"Rust subdirectory: {rust_subdir}")

        if detect_nix_requirements(repo_path, logger):
            return generate_nix_rust_dockerfile(repo_path, logger, subdir=rust_subdir, **kwargs)
        return generate_rust_dockerfile(repo_path, logger, repo_full_name=repo_full_name, subdir=rust_subdir, **kwargs)

    elif language == "csharp":
        dotnet_subdir = "dotnet" if (repo_path / "dotnet").is_dir() else None
        return generate_csharp_dockerfile(repo_path, logger, subdir=dotnet_subdir, **kwargs)

    elif language == "ruby":
        return generate_ruby_dockerfile(repo_path, logger, **kwargs)

    elif language == "php":
        return generate_php_dockerfile(repo_path, logger, **kwargs)

    elif language == "c":
        return generate_c_dockerfile(repo_path, logger, **kwargs)

    else:
        logger.warning(f"No generator for {language}, using Python template")
        return generate_python_dockerfile(repo_path, logger, **kwargs)


def setup_buildx_builder(logger: logging.Logger) -> bool:
    """Buildx setup (no-op for single-arch builds)."""
    return False


def cleanup_buildx_builder(logger: logging.Logger) -> None:
    """Buildx cleanup (no-op for single-arch builds)."""
    pass


def build_docker_image(
    repo_path: Path,
    base_commit: str,
    language: str,
    logger: logging.Logger,
    pr_number: Optional[int] = None,
    build_args: Optional[dict] = None,
    repo_full_name: Optional[str] = None,
    no_cache: bool = False,
    use_multiarch: Optional[bool] = None,
    platforms: Optional[list] = None
) -> Optional[str]:
    """
    Build Docker image at BASE commit.

    Args:
        repo_path: Path to repository (at BASE commit)
        base_commit: Base commit SHA
        language: Repository language
        logger: Logger instance
        pr_number: PR number for image tagging
        build_args: Additional build arguments
        repo_full_name: Full repository name
        no_cache: Force rebuild without cache

    Returns:
        Image tag if successful, None otherwise
    """
    logger.info(f"Building Docker image @ {base_commit[:12]}")

    # Determine repo URL
    repo_url = ""
    if repo_full_name:
        repo_url = f"https://github.com/{repo_full_name}.git"
    else:
        exit_code, stdout, _ = run_command(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            logger=logger
        )
        if exit_code == 0:
            repo_url = stdout.strip()

    # Generate Dockerfile
    dockerfile = generate_dockerfile(
        repo_path, language, logger,
        repo_full_name=repo_full_name,
        base_commit=base_commit,
        repo_url=repo_url
    )

    # Image tag
    if pr_number:
        image_tag = f"pr-eval:pr-{pr_number}-base-{base_commit[:12]}"
    else:
        image_tag = f"pr-eval:base-{base_commit[:12]}"

    logger.info(f"Image: {image_tag}")

    # Build arguments
    args = build_args or {}
    args["REPO_URL"] = repo_url
    args["BASE_COMMIT"] = base_commit
    args["JOBS"] = str(DOCKER_BUILD_JOBS)

    # Build command
    cmd = ["docker", "build", "-f", str(dockerfile), "-t", image_tag]
    if no_cache:
        cmd.append("--no-cache")
    for k, v in args.items():
        cmd.extend(["--build-arg", f"{k}={v}"])
    cmd.append(".")

    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "1"

    logger.info(f"Command: {' '.join(cmd)}")

    exit_code, stdout, stderr = run_command(
        cmd, cwd=repo_path, env=env, logger=logger, timeout=DOCKER_TIMEOUT_BUILD
    )

    if exit_code != 0:
        logger.error(f"Build failed (exit {exit_code})")
        if stderr:
            logger.error(f"stderr:\n{stderr[-3000:]}")

        # Save error log
        try:
            (repo_path / "docker_build_error.log").write_text(
                f"Exit: {exit_code}\nTag: {image_tag}\nCmd: {' '.join(cmd)}\n\n"
                f"STDOUT:\n{stdout or '(empty)'}\n\nSTDERR:\n{stderr or '(empty)'}"
            )
        except Exception:
            pass
        return None

    logger.info(f"Built: {image_tag}")
    return image_tag


def check_docker_available(logger: logging.Logger) -> bool:
    """Check if Docker is available."""
    exit_code, _, _ = run_command(["docker", "version"], logger=logger, timeout=30)
    if exit_code != 0:
        logger.error("Docker not available")
        return False
    return True


def save_and_compress_image(
    image_tag: str,
    output_dir: Path,
    base_commit: str,
    logger: logging.Logger,
    repo_full_name: Optional[str] = None,
    repo_path: Optional[Path] = None,
    save_dockerfile: bool = True
) -> Optional[str]:
    """
    Save Docker image to tar file.

    Args:
        image_tag: Docker image tag
        output_dir: Output directory
        base_commit: Base commit SHA
        logger: Logger instance
        repo_full_name: Full repository name
        repo_path: Path to repository
        save_dockerfile: Copy Dockerfile alongside tar

    Returns:
        Image URI (file path) or None
    """
    import shutil

    logger.info(f"Saving: {image_tag}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Filenames
    if repo_full_name:
        safe_name = repo_full_name.replace("/", "_")
        tar_file = output_dir / f"{safe_name}-{base_commit}.tar"
        df_dest = output_dir / f"{safe_name}-{base_commit}.Dockerfile"
    else:
        tar_file = output_dir / f"base-{base_commit}.tar"
        df_dest = output_dir / f"base-{base_commit}.Dockerfile"

    # Save image
    exit_code, _, stderr = run_command(
        ["docker", "save", "-o", str(tar_file), image_tag],
        logger=logger, timeout=1800
    )

    if exit_code != 0:
        logger.error(f"Save failed: {stderr}")
        return None

    size_mb = tar_file.stat().st_size / (1024 * 1024)
    logger.info(f"Saved: {size_mb:.1f} MB")

    # Copy Dockerfile
    if save_dockerfile and repo_path:
        df_src = repo_path / "Dockerfile.pr-eval"
        if df_src.exists():
            try:
                shutil.copy2(df_src, df_dest)
                logger.info(f"Dockerfile: {df_dest}")
            except Exception as e:
                logger.warning(f"Failed to copy Dockerfile: {e}")

    return f"file://{tar_file.absolute()}"
