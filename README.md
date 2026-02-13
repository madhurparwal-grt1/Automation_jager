# Universal Multi-Architecture Docker Image Builder

This tool builds universal Docker images that support both **linux/amd64** (Intel/AMD) and **linux/arm64** (ARM) architectures in a single OCI-compliant tarball.

## Overview

The script uses Docker Buildx to create multi-architecture images that work seamlessly across different CPU architectures. When you load the resulting `.tar` file on any machine, Docker automatically selects the correct architecture for that host.

## Prerequisites

### System Requirements
- Docker installed and running
- Docker Buildx plugin installed
- `sudo` access (if Docker requires it)
- Python 3.6 or higher

### Verify Prerequisites

```bash
# Check Docker
docker --version
docker info

# Check Buildx
docker buildx version

# Check Python
python3 --version
```

### Installing Docker Buildx

If Buildx is not available:

**Linux:**
```bash
# Docker Desktop includes Buildx by default
# For Docker Engine, install the plugin:
sudo apt-get update
sudo apt-get install docker-buildx-plugin
```

**macOS:**
```bash
# Included in Docker Desktop for Mac
# Or install via Homebrew:
brew install docker-buildx
```

## Installation

1. Extract the package:
```bash
unzip docker_buildx_package.zip
cd docker_buildx_package
```

2. Verify the structure:
```
docker_buildx_package/
├── build_universal_image.py          # Main script
├── automation_script/
│   ├── __init__.py                   # Package init
│   └── utils.py                      # Helper utilities
└── README.md                          # This file
```

## Usage

### Basic Usage

```bash
python3 build_universal_image.py \
    --dockerfile <path-to-dockerfile> \
    --output <output.tar> \
    --repo_url <git-repo-url> \
    --commit <commit-sha>
```

### Example

```bash
python3 build_universal_image.py \
    --dockerfile ./Dockerfile \
    --output ./my-app.tar \
    --repo_url https://github.com/example/my-app.git \
    --commit abc123def456
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--dockerfile` | Yes | Path to the Dockerfile to build |
| `--output` | Yes | Path for the output .tar file (OCI format) |
| `--repo_url` | Yes | Repository URL (passed as REPO_URL build arg) |
| `--commit` | Yes | Commit SHA (passed as BASE_COMMIT build arg) |
| `--context` | No | Build context directory (default: current directory) |
| `--builder-name` | No | Name of the buildx builder (default: velora-builder) |
| `--verbose` or `-v` | No | Enable verbose logging |

### Advanced Examples

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

**Using with sudo (if Docker requires it):**
```bash
# The script automatically uses sudo for Docker commands when needed
python3 build_universal_image.py \
    --dockerfile ./Dockerfile \
    --output ./image.tar \
    --repo_url https://github.com/example/repo.git \
    --commit abc123
```

## What the Script Does

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

### Docker Permission Denied

If you see permission errors:
```bash
# Add your user to docker group
sudo usermod -aG docker $USER

# Then log out and back in, or run:
newgrp docker
```

### Buildx Not Available

```bash
# Install Docker Buildx plugin
sudo apt-get update
sudo apt-get install docker-buildx-plugin

# Or on macOS with Homebrew:
brew install docker-buildx
```

### Builder Creation Fails

```bash
# Remove existing builder and let script recreate it
docker buildx rm velora-builder

# Then run the script again
```

### Build Fails for One Architecture

This can happen if:
- Base image doesn't support that architecture
- Architecture-specific binaries are being installed
- Solution: Check Dockerfile for architecture-specific commands

### Output File Not Created

Check:
- Disk space available
- Output directory exists and is writable
- Build completed without errors in the logs

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

## Support

For issues or questions:
1. Check Docker and Buildx are properly installed
2. Review the verbose output with `--verbose` flag
3. Verify Dockerfile accepts REPO_URL and BASE_COMMIT args
4. Check build logs in the terminal output

## License

This tool is provided as-is for building multi-architecture Docker images.

---

**Happy Building!** 🚀
