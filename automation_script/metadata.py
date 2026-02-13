"""
Metadata generation for the automation workflow.

Generates the final instance.json file with all required fields.
"""

import json
import logging
import random
import time
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from .config import PRInfo, TestResult, WorkflowMetadata
from .github_api import get_problem_statement


def generate_instance_id() -> str:
    """
    Generate a 16-digit unique ID using timestamp.
    Format: timestamp in milliseconds (13 digits) + random 3 digits
    """
    timestamp_ms = int(time.time() * 1000)  # 13 digits
    random_suffix = random.randint(100, 999)  # 3 digits
    return f"{timestamp_ms}{random_suffix}"


def generate_metadata(
    pr_info: PRInfo,
    base_commit: str,
    pr_commit: str,
    fail_to_pass: List[str],
    pass_to_pass: List[str],
    language: str,
    test_command: str,
    image_uri: Optional[str],
    patch: str,
    test_patch: str,
    metadata_path: Path,
    logger: logging.Logger
) -> WorkflowMetadata:
    """
    Generate the metadata JSON file with all required fields.

    Schema:
    {
        "instance_id": "",
        "repo": "",
        "base_commit": "",
        "problem_statement": "",
        "hints_text": "",
        "FAIL_TO_PASS": "",
        "PASS_TO_PASS": "",
        "language": "",
        "test_command": "",
        "test_output_parser": "",
        "image_storage_uri": "",
        "patch": "",
        "test_patch": ""
    }

    Args:
        pr_info: Parsed PR information
        base_commit: Base commit SHA
        pr_commit: PR commit SHA
        fail_to_pass: List of tests that failed on base but pass on PR
        pass_to_pass: List of tests that pass on both base and PR
        language: Repository language
        test_command: Test command used
        image_uri: Docker image URI
        patch: Code-only git diff (excludes test files)
        test_patch: Test-only git diff (only test files)
        metadata_path: Directory to save metadata
        logger: Logger instance
        
    Note:
        The patch and test_patch should be properly separated:
        - patch: Contains ONLY code changes (no test files)
        - test_patch: Contains ONLY test file changes
        - No file should appear in both patches

    Returns:
        WorkflowMetadata object
    """
    logger.info("Generating instance metadata")

    # Determine test output parser based on test command
    if "pytest" in test_command:
        test_output_parser = "pytest_json"
    elif "unittest" in test_command:
        test_output_parser = "unittest"
    elif "jest" in test_command or "npm test" in test_command:
        test_output_parser = "jest"
    elif "go test" in test_command:
        test_output_parser = "go_test"
    elif "cargo test" in test_command:
        test_output_parser = "cargo_test"
    else:
        test_output_parser = "generic"

    # Fetch PR description from GitHub API
    repo_full = f"{pr_info.owner}/{pr_info.repo}"
    pr_url = f"https://github.com/{repo_full}/pull/{pr_info.pr_number}"
    problem_statement = get_problem_statement(
        repo=repo_full,
        pr_number=pr_info.pr_number,
        pr_url=pr_url,
        logger=logger
    )

    # Create metadata object
    metadata = WorkflowMetadata(
        instance_id=generate_instance_id(),
        repo=repo_full,
        base_commit=base_commit,
        problem_statement=problem_statement,
        hints_text="",
        FAIL_TO_PASS=json.dumps(fail_to_pass),
        PASS_TO_PASS=json.dumps(pass_to_pass),
        language=language,
        test_command=test_command,
        test_output_parser=test_output_parser,
        image_storage_uri=image_uri or "",
        patch=patch,
        test_patch=test_patch,
    )

    # Save to file
    metadata_path.mkdir(parents=True, exist_ok=True)
    instance_file = metadata_path / "instance.json"

    with open(instance_file, "w") as f:
        json.dump(asdict(metadata), f, indent=2)

    logger.info(f"Metadata saved to {instance_file}")

    # Log summary
    logger.info("Metadata summary:")
    logger.info(f"  instance_id: {metadata.instance_id}")
    logger.info(f"  repo: {metadata.repo}")
    logger.info(f"  base_commit: {metadata.base_commit}")
    logger.info(f"  language: {metadata.language}")
    logger.info(f"  test_command: {metadata.test_command}")
    logger.info(f"  FAIL_TO_PASS count: {len(fail_to_pass)}")
    logger.info(f"  PASS_TO_PASS count: {len(pass_to_pass)}")
    logger.info(f"  image_uri: {metadata.image_storage_uri or 'N/A'}")
    logger.info(f"  patch size: {len(patch)} bytes")

    return metadata


def validate_metadata(
    metadata_path: Path,
    logger: logging.Logger
) -> bool:
    """
    Validate the generated metadata file.

    Checks:
    - File exists
    - Valid JSON
    - All required fields present
    - Fields have appropriate values

    Args:
        metadata_path: Path to metadata directory
        logger: Logger instance

    Returns:
        True if valid
    """
    logger.info("Validating metadata")

    instance_file = metadata_path / "instance.json"

    if not instance_file.exists():
        logger.error("instance.json not found")
        return False

    try:
        with open(instance_file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in instance.json: {e}")
        return False

    # Check required fields
    required_fields = [
        "instance_id",
        "repo",
        "base_commit",
        "problem_statement",
        "hints_text",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "language",
        "test_command",
        "test_output_parser",
        "image_storage_uri",
        "patch",
        "test_patch",
    ]

    missing = []
    for field in required_fields:
        if field not in data:
            missing.append(field)

    if missing:
        logger.error(f"Missing required fields: {missing}")
        return False

    # Validate field types/values
    errors = []

    if not data["instance_id"]:
        errors.append("instance_id is empty")

    if not data["repo"] or "/" not in data["repo"]:
        errors.append("repo should be in format 'owner/repo'")

    if not data["base_commit"] or len(data["base_commit"]) < 7:
        errors.append("base_commit should be a valid SHA")

    # FAIL_TO_PASS and PASS_TO_PASS should be valid JSON arrays
    for field in ["FAIL_TO_PASS", "PASS_TO_PASS"]:
        try:
            parsed = json.loads(data[field])
            if not isinstance(parsed, list):
                errors.append(f"{field} should be a JSON array")
        except json.JSONDecodeError:
            errors.append(f"{field} is not valid JSON")

    if errors:
        for error in errors:
            logger.error(f"Validation error: {error}")
        return False

    logger.info("Metadata validation passed")
    return True


def load_metadata(metadata_path: Path) -> Optional[WorkflowMetadata]:
    """
    Load metadata from instance.json.

    Args:
        metadata_path: Path to metadata directory

    Returns:
        WorkflowMetadata object or None if failed
    """
    instance_file = metadata_path / "instance.json"

    if not instance_file.exists():
        return None

    try:
        with open(instance_file) as f:
            data = json.load(f)

        return WorkflowMetadata(**data)
    except (json.JSONDecodeError, TypeError):
        return None
