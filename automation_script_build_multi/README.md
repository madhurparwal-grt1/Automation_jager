# Automation Script Build Multi

Utility functions for multi-architecture Docker image builds.

> **Navigation:** This module is part of the [Automation_jager](../README.md) project. See the [main README](../README.md) for installation and the multi-arch image builder (`build_universal_image.py`). For the PR evaluation pipeline, see [automation_script/README.md](../automation_script/README.md).

## Overview

This package provides shared utility functions used by `build_universal_image.py` and other multi-architecture build workflows. It includes command execution helpers with logging, error handling, and output capture.

## Module Structure

```
automation_script_build_multi/
├── __init__.py       # Package initialization
├── utils.py          # Command execution utilities
└── README.md         # This file
```

## Key Functions

### `run_command()`

Execute a shell command with logging and error handling.

```python
from automation_script_build_multi.utils import run_command

result = run_command("docker buildx version", capture_output=True, check=True)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str` or `list` | required | Command to execute |
| `cwd` | `str` | `None` | Working directory |
| `capture_output` | `bool` | `False` | Capture stdout/stderr |
| `check` | `bool` | `True` | Raise on non-zero exit code |
| `env` | `dict` | `None` | Environment variables |
| `shell` | `bool` | `True` | Run through shell |

### `run_command_with_output()`

Execute a command and return its stdout as a string.

```python
from automation_script_build_multi.utils import run_command_with_output

output = run_command_with_output("docker buildx ls")
```

Same parameters as `run_command()`, but always captures output and returns the stripped stdout string.

## Usage with build_universal_image.py

The `build_universal_image.py` script at the project root uses these utilities (via `automation_script.utils`) for Docker and Buildx operations:

```bash
python3 build_universal_image.py \
    --dockerfile ./Dockerfile \
    --output ./image.tar \
    --repo_url https://github.com/example/repo.git \
    --commit abc123
```

See the [main README](../README.md) for full usage details.

## Requirements

- Python 3.8+
- Docker 20.10+ (for Docker-related commands)
