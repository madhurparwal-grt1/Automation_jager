"""
Utility functions for automation scripts.
"""

import subprocess
import sys
import logging
from typing import Optional, List, Union

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def run_command(
    command: Union[str, List[str]],
    cwd: Optional[str] = None,
    capture_output: bool = False,
    check: bool = True,
    env: Optional[dict] = None,
    shell: bool = True
) -> subprocess.CompletedProcess:
    """
    Execute a shell command with logging and error handling.

    Args:
        command: Command to execute (string or list of arguments)
        cwd: Working directory for the command
        capture_output: Whether to capture stdout/stderr
        check: Whether to raise exception on non-zero exit
        env: Environment variables to use
        shell: Whether to run command through shell

    Returns:
        subprocess.CompletedProcess: Result of the command execution

    Raises:
        subprocess.CalledProcessError: If check=True and command fails
    """
    if isinstance(command, list):
        cmd_str = ' '.join(command)
    else:
        cmd_str = command

    logger.info(f"Executing: {cmd_str}")

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=capture_output,
            check=check,
            env=env,
            shell=shell,
            text=True
        )

        if capture_output and result.stdout:
            logger.debug(f"stdout: {result.stdout}")

        return result

    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}")
        if e.stdout:
            logger.error(f"stdout: {e.stdout}")
        if e.stderr:
            logger.error(f"stderr: {e.stderr}")
        raise


def run_command_with_output(
    command: Union[str, List[str]],
    cwd: Optional[str] = None,
    check: bool = True,
    env: Optional[dict] = None,
    shell: bool = True
) -> str:
    """
    Execute a shell command and return its output.

    Args:
        command: Command to execute
        cwd: Working directory
        check: Whether to raise exception on failure
        env: Environment variables
        shell: Whether to run through shell

    Returns:
        str: Command output (stdout)
    """
    result = run_command(
        command=command,
        cwd=cwd,
        capture_output=True,
        check=check,
        env=env,
        shell=shell
    )
    return result.stdout.strip() if result.stdout else ""
