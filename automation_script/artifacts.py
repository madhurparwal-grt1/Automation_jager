"""
Artifact management for the automation workflow.

Handles:
- Saving test results as JSONL
- Saving stdout/stderr logs
- Saving summary files
- Validating output files
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from .config import TestResult


def save_test_artifacts(
    test_result: TestResult,
    artifacts_path: Path,
    prefix: str,
    logger: logging.Logger
) -> Dict[str, Path]:
    """
    Save test artifacts to the artifacts directory.

    Creates:
    - {prefix}_results.jsonl: Each test as a JSON line
    - {prefix}_stdout.txt: Standard output
    - {prefix}_stderr.txt: Standard error
    - {prefix}_summary.json: Overall summary

    Args:
        test_result: Test results to save
        artifacts_path: Directory for artifacts
        prefix: File prefix (e.g., 'base' or 'pr')
        logger: Logger instance

    Returns:
        Dictionary mapping artifact type to file path
    """
    logger.info(f"Saving artifacts to {artifacts_path} with prefix '{prefix}'")

    artifacts_path.mkdir(parents=True, exist_ok=True)
    artifacts = {}

    # Save JSONL results (each test as a line)
    jsonl_path = artifacts_path / f"{prefix}_results.jsonl"
    with open(jsonl_path, "w") as f:
        for test in test_result.tests_passed:
            f.write(json.dumps({
                "test": test,
                "outcome": "passed"
            }) + "\n")
        for test in test_result.tests_failed:
            f.write(json.dumps({
                "test": test,
                "outcome": "failed"
            }) + "\n")
        for test in test_result.tests_skipped:
            f.write(json.dumps({
                "test": test,
                "outcome": "skipped"
            }) + "\n")
    artifacts["jsonl"] = jsonl_path
    logger.debug(f"Saved JSONL: {jsonl_path}")

    # Save stdout
    stdout_path = artifacts_path / f"{prefix}_stdout.txt"
    with open(stdout_path, "w") as f:
        f.write(test_result.stdout)
    artifacts["stdout"] = stdout_path

    # Save stderr
    stderr_path = artifacts_path / f"{prefix}_stderr.txt"
    with open(stderr_path, "w") as f:
        f.write(test_result.stderr)
    artifacts["stderr"] = stderr_path

    # Save summary JSON
    summary_path = artifacts_path / f"{prefix}_summary.json"
    summary = {
        "success": test_result.success,
        "exit_code": test_result.exit_code,
        "duration_seconds": round(test_result.duration, 2),
        "counts": {
            "passed": len(test_result.tests_passed),
            "failed": len(test_result.tests_failed),
            "skipped": len(test_result.tests_skipped),
            "total": (
                len(test_result.tests_passed) +
                len(test_result.tests_failed) +
                len(test_result.tests_skipped)
            )
        },
        "error_type": test_result.error_type,
        "tests_passed": test_result.tests_passed[:100],  # Limit for readability
        "tests_failed": test_result.tests_failed[:100],
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    artifacts["summary"] = summary_path
    logger.debug(f"Saved summary: {summary_path}")

    logger.info(f"Saved {len(artifacts)} artifact files")
    return artifacts


def validate_jsonl_file(file_path: Path) -> Tuple[bool, str]:
    """
    Validate a JSONL file for correct format.

    Args:
        file_path: Path to JSONL file

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not file_path.exists():
        return False, f"File not found: {file_path}"

    try:
        with open(file_path) as f:
            line_number = 0
            for line in f:
                line_number += 1
                if line.strip():  # Skip empty lines
                    json.loads(line)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON at line {line_number}: {e}"


def validate_json_file(file_path: Path) -> Tuple[bool, str]:
    """
    Validate a JSON file for correct format.

    Args:
        file_path: Path to JSON file

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not file_path.exists():
        return False, f"File not found: {file_path}"

    try:
        with open(file_path) as f:
            json.load(f)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"


def validate_artifacts(
    artifacts_path: Path,
    required_prefix: str,
    logger: logging.Logger
) -> Tuple[bool, List[str]]:
    """
    Validate all artifacts in a directory.

    Checks:
    - JSONL files are valid
    - Required files exist
    - JSON files are valid
    - Test reports exist (XML, HTML)

    Args:
        artifacts_path: Directory containing artifacts
        required_prefix: Expected file prefix (e.g., 'base', 'pr')
        logger: Logger instance

    Returns:
        Tuple of (all_valid, list_of_errors)
    """
    logger.info(f"Validating artifacts in {artifacts_path}")
    errors = []

    # Check required files exist
    required_files = [
        f"{required_prefix}_results.jsonl",
        f"{required_prefix}_summary.json",
    ]

    for filename in required_files:
        file_path = artifacts_path / filename
        if not file_path.exists():
            errors.append(f"Missing required file: {filename}")

    # Validate all JSONL files
    for jsonl_file in artifacts_path.glob("*.jsonl"):
        is_valid, error = validate_jsonl_file(jsonl_file)
        if not is_valid:
            errors.append(f"{jsonl_file.name}: {error}")

    # Validate all JSON files
    for json_file in artifacts_path.glob("*.json"):
        is_valid, error = validate_json_file(json_file)
        if not is_valid:
            errors.append(f"{json_file.name}: {error}")

    # Check for test reports (warning only)
    has_junit = list(artifacts_path.glob("*.xml"))
    has_html = list(artifacts_path.glob("*.html"))

    if not has_junit:
        logger.warning("No JUnit XML report found")
    if not has_html:
        logger.warning("No HTML report found")

    # Report results
    if errors:
        logger.error(f"Validation failed with {len(errors)} errors:")
        for error in errors:
            logger.error(f"  - {error}")
        return False, errors

    logger.info("All artifacts validated successfully")
    return True, []


def check_required_fields(
    json_path: Path,
    required_fields: List[str],
    logger: logging.Logger
) -> Tuple[bool, List[str]]:
    """
    Check that a JSON file contains required fields.

    Args:
        json_path: Path to JSON file
        required_fields: List of required field names
        logger: Logger instance

    Returns:
        Tuple of (all_present, list_of_missing_fields)
    """
    if not json_path.exists():
        return False, required_fields

    try:
        with open(json_path) as f:
            data = json.load(f)

        missing = []
        for field in required_fields:
            if field not in data:
                missing.append(field)

        if missing:
            logger.warning(f"Missing fields in {json_path.name}: {missing}")
            return False, missing

        return True, []

    except json.JSONDecodeError:
        return False, required_fields
