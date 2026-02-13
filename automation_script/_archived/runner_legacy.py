"""
Test execution and result parsing for the automation workflow.

Handles:
- Running tests with various frameworks
- Parsing test output (pytest, unittest, etc.)
- Generating test reports (JSON, HTML, JUnit XML)
- Categorizing tests as PASS_TO_PASS or FAIL_TO_PASS
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import TestResult, DEFAULT_TIMEOUT
from .utils import run_command


def build_pytest_command(
    base_command: str,
    report_dir: Path,
    verbose: bool = True
) -> str:
    """
    Build a pytest command with report generation flags.

    Args:
        base_command: Base test command (e.g., 'pytest')
        report_dir: Directory for report files
        verbose: Whether to include verbose output

    Returns:
        Full pytest command string
    """
    json_report = report_dir / "test_report.json"
    html_report = report_dir / "test_report.html"
    junit_report = report_dir / "junit.xml"

    cmd = base_command
    cmd += f" --json-report --json-report-file={json_report}"
    cmd += f" --html={html_report} --self-contained-html"
    cmd += f" --junitxml={junit_report}"

    if verbose:
        cmd += " -v"

    # Add timeout per test to avoid hanging
    cmd += " --timeout=300"

    return cmd


def parse_pytest_json_report(report_path: Path) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse pytest JSON report to extract test results.

    Args:
        report_path: Path to JSON report file

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    passed = []
    failed = []
    skipped = []

    if not report_path.exists():
        return passed, failed, skipped

    try:
        with open(report_path) as f:
            report = json.load(f)

        for test in report.get("tests", []):
            nodeid = test.get("nodeid", "unknown")
            outcome = test.get("outcome", "")

            if outcome == "passed":
                passed.append(nodeid)
            elif outcome == "failed":
                failed.append(nodeid)
            elif outcome == "skipped":
                skipped.append(nodeid)

    except (json.JSONDecodeError, KeyError) as e:
        pass

    return passed, failed, skipped


def parse_pytest_output(stdout: str, stderr: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse pytest console output to extract test results.

    Fallback when JSON report is not available.

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # Pattern: test_file.py::TestClass::test_method PASSED/FAILED/SKIPPED
    for line in combined.splitlines():
        # Look for PASSED
        match = re.search(r"(\S+::\S+)\s+PASSED", line)
        if match:
            passed.append(match.group(1))
            continue

        # Look for FAILED
        match = re.search(r"(\S+::\S+)\s+FAILED", line)
        if match:
            failed.append(match.group(1))
            continue

        # Look for SKIPPED
        match = re.search(r"(\S+::\S+)\s+SKIPPED", line)
        if match:
            skipped.append(match.group(1))
            continue

    # Also check summary line: "X passed, Y failed, Z skipped"
    summary_match = re.search(
        r"(\d+)\s+passed.*?(\d+)\s+failed.*?(\d+)\s+skipped",
        combined
    )

    return passed, failed, skipped


def parse_junit_xml(report_path: Path) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse JUnit XML report to extract test results.

    Args:
        report_path: Path to JUnit XML file

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import xml.etree.ElementTree as ET

    passed = []
    failed = []
    skipped = []

    if not report_path.exists():
        return passed, failed, skipped

    try:
        tree = ET.parse(report_path)
        root = tree.getroot()

        for testcase in root.iter("testcase"):
            name = testcase.get("classname", "") + "::" + testcase.get("name", "")

            # Check for failure/error/skipped elements
            if testcase.find("failure") is not None or testcase.find("error") is not None:
                failed.append(name)
            elif testcase.find("skipped") is not None:
                skipped.append(name)
            else:
                passed.append(name)

    except ET.ParseError:
        pass

    return passed, failed, skipped


def run_tests(
    repo_path: Path,
    language: str,
    test_command: str,
    artifacts_path: Path,
    venv_path: Optional[Path],
    logger: logging.Logger,
    timeout: int = DEFAULT_TIMEOUT
) -> TestResult:
    """
    Execute tests and capture results.

    For Python/pytest:
    - Generates JSON, HTML, and JUnit XML reports
    - Parses results from reports or console output

    Args:
        repo_path: Path to repository
        language: Repository language
        test_command: Test command to execute
        artifacts_path: Directory for test artifacts
        venv_path: Path to virtual environment (for Python)
        logger: Logger instance
        timeout: Test timeout in seconds

    Returns:
        TestResult with test outcomes
    """
    logger.info(f"Running tests: {test_command}")

    start_time = time.time()
    artifacts_path.mkdir(parents=True, exist_ok=True)

    # Prepare environment
    env = os.environ.copy()

    if venv_path and language == "python":
        if sys.platform == "win32":
            bin_path = venv_path / "Scripts"
        else:
            bin_path = venv_path / "bin"
        env["PATH"] = f"{bin_path}:{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(venv_path)

    # Build full command with report generation
    if language == "python" and "pytest" in test_command:
        full_command = build_pytest_command(test_command, artifacts_path)
    else:
        full_command = test_command

    logger.debug(f"Full test command: {full_command}")

    # Execute tests
    cmd = full_command.split()
    exit_code, stdout, stderr = run_command(
        cmd,
        cwd=repo_path,
        env=env,
        timeout=timeout,
        logger=logger
    )

    duration = time.time() - start_time

    # Parse results
    passed = []
    failed = []
    skipped = []

    if language == "python":
        # Try JSON report first
        json_report = artifacts_path / "test_report.json"
        passed, failed, skipped = parse_pytest_json_report(json_report)

        # Fall back to JUnit XML
        if not passed and not failed:
            junit_report = artifacts_path / "junit.xml"
            passed, failed, skipped = parse_junit_xml(junit_report)

        # Fall back to console output
        if not passed and not failed:
            passed, failed, skipped = parse_pytest_output(stdout, stderr)

    # Detect environment errors
    from .environment import detect_error_type
    error_type = detect_error_type(stdout, stderr)

    # Check for timeout
    if exit_code == -1 and "timed out" in stderr:
        error_type = "timeout"

    result = TestResult(
        success=exit_code == 0,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration=duration,
        tests_passed=passed,
        tests_failed=failed,
        tests_skipped=skipped,
        error_type=error_type,
    )

    logger.info(
        f"Test run complete: {len(passed)} passed, {len(failed)} failed, "
        f"{len(skipped)} skipped (exit_code={exit_code}, duration={duration:.1f}s)"
    )

    if error_type:
        logger.warning(f"Detected environment error: {error_type}")

    return result


def categorize_tests(
    base_result: TestResult,
    pr_result: TestResult,
    logger: logging.Logger
) -> Tuple[List[str], List[str]]:
    """
    Categorize tests into FAIL_TO_PASS and PASS_TO_PASS.

    FAIL_TO_PASS includes:
      1. Tests that FAILED on BASE but PASS on PR (fixed tests)
      2. Tests that DON'T EXIST on BASE but PASS on PR (new tests)
         - New tests are considered "failing" on base since they don't exist

    PASS_TO_PASS:
      - Tests that PASSED on both BASE and PR (no regressions)

    Args:
        base_result: Test results from BASE commit
        pr_result: Test results from PR commit
        logger: Logger instance

    Returns:
        Tuple of (fail_to_pass, pass_to_pass) test lists
    """
    logger.info("Categorizing tests into FAIL_TO_PASS and PASS_TO_PASS")

    # All tests known at BASE commit
    base_passed = set(base_result.tests_passed)
    base_failed = set(base_result.tests_failed)
    base_skipped = set(base_result.tests_skipped)
    base_all_tests = base_passed | base_failed | base_skipped

    # All tests known at PR commit
    pr_passed = set(pr_result.tests_passed)
    pr_failed = set(pr_result.tests_failed)

    # -------------------------------------------------------------------------
    # FAIL_TO_PASS: Tests that the PR is "fixing" or adding
    # -------------------------------------------------------------------------
    # Category 1: Tests that failed on BASE but pass on PR (actual fixes)
    fixed_tests = list(base_failed & pr_passed)

    # Category 2: NEW tests that pass on PR (didn't exist on BASE)
    # These are new test cases added by the PR - they are considered "fail" on base
    # because they don't exist there
    new_tests = list(pr_passed - base_all_tests)

    # Combine both categories
    fail_to_pass = fixed_tests + new_tests

    # -------------------------------------------------------------------------
    # PASS_TO_PASS: Tests that passed on both (regression check)
    # -------------------------------------------------------------------------
    pass_to_pass = list(base_passed & pr_passed)

    # -------------------------------------------------------------------------
    # Track regressions for logging (passed on base, failed on PR)
    # -------------------------------------------------------------------------
    regressions = list(base_passed & pr_failed)

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    logger.info(f"FAIL_TO_PASS: {len(fail_to_pass)} tests total")
    logger.info(f"  - Fixed tests (failed->passed): {len(fixed_tests)}")
    logger.info(f"  - New tests (added by PR):      {len(new_tests)}")
    logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests")

    if regressions:
        logger.warning(f"REGRESSIONS (passed->failed): {len(regressions)} tests")
        for test in regressions[:5]:  # Log first 5
            logger.warning(f"  - {test}")

    if fixed_tests:
        logger.info("Fixed tests (first 10):")
        for test in fixed_tests[:10]:
            logger.info(f"  - {test}")

    if new_tests:
        logger.info("New tests added by PR (first 10):")
        for test in new_tests[:10]:
            logger.info(f"  - {test}")

    return fail_to_pass, pass_to_pass


def is_environment_error(test_result: TestResult) -> bool:
    """
    Check if test failure is due to environment issues (not actual test failures).

    Environment errors include:
    - Missing dependencies
    - Import errors
    - Configuration issues

    Args:
        test_result: Test result to analyze

    Returns:
        True if failure appears to be environment-related
    """
    if test_result.error_type:
        return True

    # If no tests ran at all, likely an environment issue
    total_tests = (
        len(test_result.tests_passed) +
        len(test_result.tests_failed) +
        len(test_result.tests_skipped)
    )

    if total_tests == 0 and test_result.exit_code != 0:
        return True

    return False
