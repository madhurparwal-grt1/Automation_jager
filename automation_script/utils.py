"""
Utility functions for the automation workflow.

Contains:
- Command execution with timeout
- Logging setup
- File/directory helpers
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import LOG_FORMAT, DEFAULT_TIMEOUT


def setup_logging(log_file: Path, name: str = "pr_workflow") -> logging.Logger:
    """
    Configure logging to both file and console.

    Args:
        log_file: Path to the log file
        name: Logger name

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Clear existing handlers
    logger.handlers.clear()

    # File handler - captures everything (DEBUG and above)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FORMAT))

    # Console handler - INFO and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(LOG_FORMAT))

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


def run_command(
    cmd: List[str],
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT,
    env: Optional[Dict[str, str]] = None,
    capture_output: bool = True,
    logger: Optional[logging.Logger] = None
) -> Tuple[int, str, str]:
    """
    Execute a shell command with timeout and optional logging.

    Args:
        cmd: Command and arguments as a list
        cwd: Working directory
        timeout: Timeout in seconds
        env: Additional environment variables
        capture_output: Whether to capture stdout/stderr
        logger: Logger for debugging

    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    if logger:
        logger.debug(f"Running: {' '.join(cmd)}")
        if cwd:
            logger.debug(f"  in directory: {cwd}")

    # Merge environment
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            capture_output=capture_output,
            text=True,
            env=merged_env,
        )
        return result.returncode, result.stdout or "", result.stderr or ""

    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout}s"
        if logger:
            logger.error(msg)
        return -1, "", msg

    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]}"
        if logger:
            logger.error(msg)
        return -1, "", msg

    except Exception as e:
        msg = f"Command failed: {e}"
        if logger:
            logger.error(msg)
        return -1, "", msg


def ensure_directory(path: Path) -> Path:
    """Create directory if it doesn't exist and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_python_executable(venv_path: Optional[Path] = None) -> str:
    """Get the Python executable path, optionally from a venv."""
    if venv_path:
        if sys.platform == "win32":
            return str(venv_path / "Scripts" / "python")
        return str(venv_path / "bin" / "python")
    return sys.executable


def get_pip_executable(venv_path: Optional[Path] = None) -> str:
    """Get the pip executable path, optionally from a venv."""
    if venv_path:
        if sys.platform == "win32":
            return str(venv_path / "Scripts" / "pip")
        return str(venv_path / "bin" / "pip")
    return f"{sys.executable} -m pip"
