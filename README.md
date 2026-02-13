# Automation_jager

**Automated PR Evaluation and Multi-Architecture Docker Image Builder**

This project provides a comprehensive automation system for:
- Building universal Docker images supporting **linux/amd64** and **linux/arm64** architectures
- Automated PR (Pull Request) evaluation with test execution and validation
- Docker-based test harness for reproducible testing environments

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Project Structure](#project-structure)
- [Usage](#usage)
- [Prerequisites](#prerequisites)
- [Troubleshooting](#troubleshooting)

## Features

### 🚀 Multi-Architecture Docker Builds
- Build universal Docker images that work on both Intel/AMD and ARM architectures
- Single OCI-compliant tarball containing both architecture variants
- Automatic architecture selection when loading images

### 🔬 PR Evaluation System
- Automated PR cloning and evaluation
- Language and test framework detection
- Docker-based isolated test execution
- Comprehensive test result categorization and metadata generation
- Support for Python, JavaScript, TypeScript, Go, Rust, Java, C#, and Ruby

### 🛠️ Robust Automation
- Self-healing Docker builds
- Configurable test execution with retries
- Detailed logging and error reporting
- Workspace management and cleanup

## Quick Start

### Automatic Installation (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd Automation_jager

# Run the installation script
chmod +x install.sh
./install.sh
```

The installation script will automatically:
- Detect your operating system
- Install Python 3.8+, Docker, Docker Buildx, and GitHub CLI
- Configure Docker permissions
- Install Python dependencies

### Manual Installation

See [INSTALL.txt](INSTALL.txt) for detailed manual installation instructions.

## Installation

This project provides multiple ways to install dependencies:

### Option 1: Automated Installation (Recommended)

```bash
./install.sh
```

Interactive script that installs all required dependencies automatically.

### Option 2: Manual Installation

Follow the comprehensive guide in [INSTALL.txt](INSTALL.txt) for platform-specific manual installation steps.

### Option 3: Python Dependencies Only

```bash
python3 -m pip install -r requirements.txt
```

Note: This project uses Python standard library modules, so no external packages are required.

## Prerequisites

### System Requirements
- **Python 3.8 or higher**
- **Docker 20.10 or higher**
- **Docker Buildx plugin**
- **GitHub CLI** (optional but recommended for PR operations)
- 4GB RAM (8GB recommended)
- 10GB free disk space

### Supported Operating Systems
- macOS 10.15 or higher
- Ubuntu 20.04 or higher
- Debian 10 or higher
- Fedora 35 or higher
- RHEL/CentOS 8 or higher

### Verify Installation

```bash
# Check Python
python3 --version  # Should be 3.8+

# Check Docker
docker --version
docker info

# Check Buildx
docker buildx version

# Check GitHub CLI (optional)
gh --version
```

## Project Structure

```
Automation_jager/
├── build_universal_image.py      # Multi-arch Docker image builder
├── install.sh                     # Automated installation script
├── requirements.txt               # Python dependencies
├── INSTALL.txt                    # Detailed installation guide
├── README.md                      # This file
│
├── automation_script/             # Main automation package
│   ├── __init__.py
│   ├── main_orchestrator.py      # Main entry point for PR evaluation
│   ├── part1_build_and_base.py   # Docker build and base testing
│   ├── part2_patch_and_evaluate.py  # Patch application and evaluation
│   │
│   ├── config.py                  # Configuration and constants
│   ├── docker_builder_new.py     # Docker image generation
│   ├── docker_runner.py           # Container test execution
│   ├── docker_healing.py          # Self-healing Docker builds
│   │
│   ├── git_operations.py          # Git utilities
│   ├── git_wrappers.py            # High-level Git operations
│   ├── github_api.py              # GitHub API interactions
│   │
│   ├── language_detection.py     # Language/framework detection
│   ├── environment.py             # Environment setup
│   ├── test_results.py            # Test result processing
│   ├── test_targeting.py          # Test identification
│   │
│   ├── metadata_generator.py     # Metadata creation
│   ├── collect_29_fields.py      # Extended metadata collection
│   ├── artifacts.py               # Artifact management
│   ├── organize_outputs.py       # Output organization
│   │
│   ├── cleanup.py                 # Workspace cleanup
│   ├── utils.py                   # Utility functions
│   └── validate_fix.py            # Fix validation
│
└── automation_script_build_multi/ # Multi-build utilities
    ├── __init__.py
    └── utils.py
```

## Usage

### 1. PR Evaluation System

The main orchestrator provides automated PR evaluation with test execution:

#### Full Workflow (Build + Test + Evaluate)

```bash
python3 -m automation_script.main_orchestrator \
    https://github.com/owner/repo/pull/123 \
    /path/to/workspace
```

#### With Language/Test Command Overrides

```bash
python3 -m automation_script.main_orchestrator \
    --language rust \
    --test-cmd "cargo test --manifest-path engine/Cargo.toml" \
    https://github.com/owner/repo/pull/123 \
    /path/to/workspace
```

#### Part 1 Only (Build Docker Image + Base Tests)

```bash
python3 -m automation_script.main_orchestrator \
    --part1-only \
    https://github.com/owner/repo/pull/123 \
    /path/to/workspace
```

#### Part 2 Only (Patch + Evaluate)

```bash
python3 -m automation_script.main_orchestrator \
    --part2-only \
    /path/to/workspace
```

#### Performance Options

```bash
# Fast mode with shallow clone
python3 -m automation_script.main_orchestrator \
    --shallow-clone \
    https://github.com/owner/repo/pull/123 \
    /path/to/workspace

# Reuse existing Docker image
python3 -m automation_script.main_orchestrator \
    --reuse-image pr-eval:base-abc123 \
    https://github.com/owner/repo/pull/123 \
    /path/to/workspace
```

#### Get Help

```bash
python3 -m automation_script.main_orchestrator --help
```

### 2. Universal Docker Image Builder

Build multi-architecture Docker images:

#### Basic Usage

```bash
python3 build_universal_image.py \
    --dockerfile <path-to-dockerfile> \
    --output <output.tar> \
    --repo_url <git-repo-url> \
    --commit <commit-sha>
```

#### Example

```bash
python3 build_universal_image.py \
    --dockerfile ./Dockerfile \
    --output ./my-app.tar \
    --repo_url https://github.com/example/my-app.git \
    --commit abc123def456
```

#### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--dockerfile` | Yes | Path to the Dockerfile to build |
| `--output` | Yes | Path for the output .tar file (OCI format) |
| `--repo_url` | Yes | Repository URL (passed as REPO_URL build arg) |
| `--commit` | Yes | Commit SHA (passed as BASE_COMMIT build arg) |
| `--context` | No | Build context directory (default: current directory) |
| `--builder-name` | No | Name of the buildx builder (default: velora-builder) |
| `--verbose` or `-v` | No | Enable verbose logging |

#### Advanced Examples

**With custom build context:**
```bash
python3 build_universal_image.py \
    --dockerfile ./docker/Dockerfile.prod \
    --output ./dist/image.tar \
    --repo_url https://github.com/myorg/myrepo.git \
    --commit $(git rev-parse HEAD) \
    --context ./src
```

**With verbose logging:**
```bash
python3 build_universal_image.py \
    --dockerfile ./Dockerfile \
    --output ./image.tar \
    --repo_url https://github.com/example/repo.git \
    --commit abc123 \
    --verbose
```

**Get Help:**
```bash
python3 build_universal_image.py --help
```

## How It Works

### PR Evaluation Workflow

1. **Part 1: Build + Base Testing**
   - Clone repository and fetch PR references
   - Detect language and test framework
   - Build Docker image with all dependencies
   - Run base tests (without PR changes)
   - Save state for Part 2

2. **Part 2: Patch + Evaluation**
   - Generate patch files (full, test-only, code-only)
   - Verify patches apply cleanly
   - Run test-patch-only tests (should FAIL)
   - Run full-patch tests (should PASS)
   - Categorize tests (F2P, P2P, etc.)
   - Generate comprehensive metadata
   - Organize outputs and artifacts

### Multi-Architecture Docker Build

1. **Pre-flight Checks**: Verifies Docker and Buildx are available
2. **Builder Setup**: Creates or uses a Docker Buildx builder with docker-container driver
3. **Multi-Arch Build**: Builds for both linux/amd64 and linux/arm64 simultaneously
4. **Caching**: Uses local cache (`.buildx-cache/`) to speed up subsequent builds
5. **OCI Output**: Creates a single universal `.tar` file containing both architectures

## Using the Output Image

### Loading the Image

The output `.tar` file is OCI-compliant and works on any architecture:

```bash
# Load the image
docker load < my-app.tar

# Docker automatically selects the correct architecture
# - On Intel/AMD machines: uses linux/amd64 variant
# - On ARM machines (Graviton, Apple Silicon): uses linux/arm64 variant
```

### Verifying the Image

```bash
# List loaded images
docker images

# Inspect the image to see supported platforms
docker image inspect <image-name> | grep Architecture
```

### Distributing the Image

The `.tar` file is portable and can be:
- Copied to other machines
- Uploaded to cloud storage
- Distributed to colleagues
- Imported on any Docker-compatible system

## Build Cache

The script uses a local cache directory (`.buildx-cache/`) to speed up builds:

- **Location**: `./.buildx-cache` in the working directory
- **Purpose**: Caches layers to speed up subsequent builds
- **Cleanup**: Delete the directory to clear cache if needed

```bash
# Clear build cache
rm -rf .buildx-cache
```

## Dockerfile Requirements

Your Dockerfile should:
- Accept `REPO_URL` and `BASE_COMMIT` as build arguments:
  ```dockerfile
  ARG REPO_URL
  ARG BASE_COMMIT
  ```
- Use multi-architecture compatible base images (e.g., `ubuntu:24.04`)
- Avoid architecture-specific binaries unless conditionally installed

### Example Dockerfile Structure

```dockerfile
FROM ubuntu:24.04

ARG REPO_URL
ARG BASE_COMMIT

# Your build steps here
RUN apt-get update && apt-get install -y git

# Clone repository
RUN git clone "${REPO_URL}" /app
WORKDIR /app
RUN git checkout "${BASE_COMMIT}"

# Rest of your build...
```

## Troubleshooting

### Common Issues

#### Docker Permission Denied

```bash
# Add your user to docker group
sudo usermod -aG docker $USER

# Then log out and log back in, or run:
newgrp docker
```

#### Buildx Not Available

```bash
# Install Docker Buildx plugin (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install docker-buildx-plugin

# Or on macOS with Homebrew:
brew install docker-buildx
```

#### Language/Test Detection Fails

Use manual overrides:

```bash
python3 -m automation_script.main_orchestrator \
    --language python \
    --test-cmd "pytest" \
    https://github.com/owner/repo/pull/123 \
    /path/to/workspace
```

#### Docker Build Fails

Check logs and consider using self-healing features (automatic) or manual intervention:

```bash
# View build logs
cat /path/to/workspace/<pr_folder>/logs/part1_build_and_base.log
```

#### Builder Creation Fails

```bash
# Remove existing builder and let script recreate it
docker buildx rm multi-arch-builder

# Then run the script again
```

#### Build Fails for One Architecture

This can happen if:
- Base image doesn't support that architecture
- Architecture-specific binaries are being installed
- Solution: Check Dockerfile for architecture-specific commands

#### Slow Cloning for Large Repositories

Use shallow clone:

```bash
python3 -m automation_script.main_orchestrator \
    --shallow-clone \
    https://github.com/owner/repo/pull/123 \
    /path/to/workspace
```

### Getting More Help

1. Check [INSTALL.txt](INSTALL.txt) for detailed installation guidance
2. Run scripts with `--help` flag for usage information
3. Review logs in workspace directory under `logs/`
4. Verify all prerequisites are properly installed

## Performance Tips

1. **Use Build Cache**: The script caches layers automatically
2. **Parallel Builds**: Both architectures build in parallel
3. **Network Speed**: First build downloads images; subsequent builds are faster
4. **Use .dockerignore**: Exclude unnecessary files from build context

## Architecture Support

The script builds for:
- **linux/amd64**: Intel and AMD x86_64 processors
- **linux/arm64**: ARM 64-bit processors (AWS Graviton, Apple Silicon, etc.)

To modify architectures, edit the `PLATFORMS` constant in the script:
```python
PLATFORMS = "linux/amd64,linux/arm64"
```

## FAQ

**Q: Can I build for just one architecture?**  
A: Yes, modify `PLATFORMS = "linux/amd64"` in the script.

**Q: How large will the output file be?**  
A: Approximately 2x the size of a single-architecture image (contains both variants).

**Q: Can I push to Docker Hub instead of creating a tar file?**  
A: Yes, replace `--output=type=oci,dest=<file>` with `--push` and tag the image.

**Q: Does this work with private registries?**  
A: Yes, ensure you're logged in with `docker login` before running.

**Q: Can I add more architectures?**  
A: Yes, add to PLATFORMS (e.g., `"linux/amd64,linux/arm64,linux/arm/v7"`), but ensure base images support them.

## Supported Languages

The PR evaluation system automatically detects and supports:

- **Python** - pytest, unittest
- **JavaScript/TypeScript** - npm test, jest, mocha
- **Go** - go test
- **Rust** - cargo test
- **Java** - maven, gradle
- **C#** - dotnet test
- **Ruby** - rake test, rspec

If auto-detection fails, use `--language` and `--test-cmd` flags to override.

## Output Artifacts

After successful PR evaluation, the workspace contains:

```
workspace/<pr_folder>/
├── artifacts/              # Test results and outputs
├── logs/                   # Execution logs
├── metadata.json           # Comprehensive PR metadata
├── patches/                # Generated patch files
├── repo/                   # Cloned repository
└── outputs/                # Organized final outputs
```

## Advanced Configuration

### Environment Variables

- `DOCKER_BUILDKIT=1` - Enable BuildKit (recommended)
- `DOCKER_BUILD_JOBS` - Number of parallel build jobs

### Custom Configurations

Edit `automation_script/config.py` for:
- Docker build settings
- Timeout configurations
- Test execution parameters
- Logging preferences

## Contributing

When contributing:
1. Follow existing code style and structure
2. Test changes thoroughly with different PR types
3. Update documentation for new features
4. Ensure all linters pass

## Support

For issues or questions:

1. Check [INSTALL.txt](INSTALL.txt) for installation issues
2. Review [Troubleshooting](#troubleshooting) section
3. Run scripts with `--help` or `--verbose` flags
4. Check logs in workspace directory
5. Verify all prerequisites are installed correctly

## Additional Resources

- **Installation Guide**: See [INSTALL.txt](INSTALL.txt)
- **Python Dependencies**: See [requirements.txt](requirements.txt)
- **Automated Install**: Run [install.sh](install.sh)

## License

This tool is provided as-is for automated PR evaluation and multi-architecture Docker image building.

---

**Happy Automating!** 🚀
