#!/usr/bin/env python3
"""
Container-side test runner.

This script runs INSIDE Docker containers to:
1. Apply patches (for patched mode)
2. Run tests
3. Save results to mounted workspace

Directory Structure (inside container):
- /app/repo           - Repository source code (from image build)
- /saved/ENV          - Language toolchain and cached dependencies
- /saved/venv/ENV     - Python virtual environment (Python only)
- /workspace          - Evaluation workspace (mounted from host)

This script must be minimal and self-contained since it runs
in the container environment.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple


def normalize_test_name(test_name: str) -> str:
    """
    Normalize a test name by stripping timing suffixes and extra whitespace.
    
    This handles test output formats that include timing information, which can vary
    between runs and cause the same test to appear as different tests.
    
    Examples:
    - "it can be instantiated                      0.01s" -> "it can be instantiated"
    - "test name  0.02s  " -> "test name"
    - "test name (123ms)" -> "test name"  (Jest format)
    - "test name" -> "test name" (no change)
    
    Args:
        test_name: Raw test name that may include timing suffix
        
    Returns:
        Normalized test name with timing stripped
    """
    if not test_name:
        return test_name
    
    # Strip trailing whitespace first
    name = test_name.rstrip()
    
    # Pattern 1: Pest/Mocha style - trailing "0.01s" or "12.34s"
    # Matches: whitespace + digits.digits + 's' at end
    name = re.sub(r'\s+\d+\.\d+s$', '', name)
    
    # Pattern 2: Jest style - "(123ms)" or "(1.23s)" at end
    # Matches: whitespace + (digits + 'ms' or 's') at end
    name = re.sub(r'\s*\(\d+(?:\.\d+)?\s*m?s\)$', '', name)
    
    # Pattern 3: Condensed whitespace in the middle (caused by timing removal)
    # Normalize multiple spaces to single space
    name = re.sub(r'\s{2,}', ' ', name)
    
    return name.strip()


def normalize_test_list(tests: List[str]) -> List[str]:
    """
    Normalize a list of test names and remove duplicates caused by timing variations.
    
    Args:
        tests: List of raw test names
        
    Returns:
        List of normalized unique test names
    """
    seen = set()
    result = []
    for test in tests:
        normalized = normalize_test_name(test)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def run_command(cmd: List[str], cwd: Path = None, timeout: int = 600) -> Tuple[int, str, str]:
    """Execute a command with timeout."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def apply_patch(patch_path: Path, repo_path: Path) -> Tuple[bool, str]:
    """
    Apply a git patch to the repository.

    Args:
        patch_path: Path to patch file
        repo_path: Repository path

    Returns:
        Tuple of (success, error_message)
    """
    print(f"Applying patch: {patch_path}")

    if not patch_path.exists():
        return False, f"Patch file not found: {patch_path}"

    # First, ensure we're at a clean state
    # Note: Use -fd instead of -fdx to preserve ignored files like vendor/, node_modules/, etc.
    run_command(["git", "reset", "--hard", "HEAD"], cwd=repo_path)
    run_command(["git", "clean", "-fd"], cwd=repo_path)

    # Apply the patch
    exit_code, stdout, stderr = run_command(
        ["git", "apply", "--whitespace=fix", str(patch_path)],
        cwd=repo_path,
        timeout=60
    )

    if exit_code != 0:
        error_msg = f"Patch application failed: {stderr}"
        print(error_msg, file=sys.stderr)
        return False, error_msg

    print("Patch applied successfully")
    return True, ""


def parse_pytest_output(stdout: str, stderr: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse pytest output to extract test results.

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re

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

    return passed, failed, skipped


def parse_go_test_output(stdout: str, stderr: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse go test output to extract test results.

    Supports both verbose (-v) and JSON (-json) output formats.

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re

    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # Try JSON format first (go test -json)
    if combined.strip().startswith('{'):
        test_results = {}  # Map test name to outcome

        for line in combined.splitlines():
            try:
                event = json.loads(line)
                action = event.get("Action")
                test = event.get("Test")

                if not test:
                    continue

                # Build full test name with package
                package = event.get("Package", "")
                if package:
                    full_test = f"{package}.{test}"
                else:
                    full_test = test

                if action == "pass":
                    test_results[full_test] = "pass"
                elif action == "fail":
                    test_results[full_test] = "fail"
                elif action == "skip":
                    test_results[full_test] = "skip"
            except:
                continue

        for test, outcome in test_results.items():
            if outcome == "pass":
                passed.append(test)
            elif outcome == "fail":
                failed.append(test)
            elif outcome == "skip":
                skipped.append(test)

        return passed, failed, skipped

    # Fall back to verbose format (go test -v)
    # Pattern: --- PASS: TestName (0.01s)
    for line in combined.splitlines():
        # Passing test
        match = re.search(r'^---\s+PASS:\s+(\S+)', line)
        if match:
            test_name = match.group(1)
            passed.append(test_name)
            continue

        # Failing test
        match = re.search(r'^---\s+FAIL:\s+(\S+)', line)
        if match:
            test_name = match.group(1)
            failed.append(test_name)
            continue

        # Skipped test
        match = re.search(r'^---\s+SKIP:\s+(\S+)', line)
        if match:
            test_name = match.group(1)
            skipped.append(test_name)
            continue

    return passed, failed, skipped


def parse_phpunit_output(stdout: str, stderr: str, test_command: str = "", output_dir: Path = None) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse PHPUnit output to extract test results.
    
    PHPUnit output formats:
    1. Default: Shows dots (.) for passing, F for failure, S for skipped
    2. Testdox: Shows test names in readable format
    3. JUnit XML: Machine-readable format (needs file parsing)
    
    Test name format (matching harness standard):
    - Simple: "Namespace\\ClassName::testMethodName"
    - With data set: "Namespace\\ClassName::testMethodNameWithDataSet\"dataSetName\""
    
    Testdox output example:
        Abstract Csv (League\Csv\AbstractCsv)
         ✔ Stream filter mode with data set "Reader with stream capability"
    
    Converts to: League\Csv\AbstractCsv::testStreamFilterModeWithDataSet"readerWithStreamCapability"
    
    Args:
        stdout: Standard output
        stderr: Standard error
        test_command: Original test command (to detect output format)
        output_dir: Output directory where testdox.txt might be located
    
    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re
    
    passed = []
    failed = []
    skipped = []
    
    combined = stdout + stderr
    
    def convert_testdox_to_method_name(testdox_name: str) -> str:
        """
        Convert testdox human-readable test name to PHPUnit method format.
        
        Examples:
        - "Create from file object" -> "testCreateFromFileObject"
        - "Stream filter mode with data set \"Reader with stream capability\"" 
          -> "testStreamFilterModeWithDataSet\"readerWithStreamCapability\""
        - "It can convert with valid value with data set \"it can convert an integer\""
          -> "testItCanConvertWithValidValueWithDataSet\"itCanConvertAnInteger\""
        """
        # Check if there's a data set suffix
        data_set_match = re.search(r'\s+with\s+data\s+set\s+["\']([^"\']+)["\']$', testdox_name, re.IGNORECASE)
        
        if data_set_match:
            # Extract the data set name and the base test name
            data_set_name = data_set_match.group(1)
            base_name = testdox_name[:data_set_match.start()]
            
            # Convert base name to camelCase method name
            words = base_name.split()
            if words:
                # First word lowercase, rest capitalized
                method_words = [words[0].lower()] + [w.capitalize() for w in words[1:]]
                method_base = "test" + "".join(method_words)
            else:
                method_base = "test"
            
            # Add "WithDataSet" suffix
            method_base += "WithDataSet"
            
            # Convert data set name to camelCase (first letter lowercase)
            ds_words = data_set_name.split()
            if ds_words:
                # Make first word lowercase, rest capitalized (camelCase)
                ds_camel = ds_words[0].lower() + "".join(w.capitalize() for w in ds_words[1:])
            else:
                ds_camel = data_set_name.lower()
            
            # Format: testMethodNameWithDataSet"dataSetName"
            return f'{method_base}"{ds_camel}"'
        else:
            # Simple test name without data set
            words = testdox_name.split()
            if words:
                # First word lowercase in method part, then capitalize rest
                method_words = [words[0].lower()] + [w.capitalize() for w in words[1:]]
                return "test" + "".join(method_words)
            return "test"
    
    # Try to read testdox.txt file if it exists (from --testdox-text option)
    testdox_content = ""
    if output_dir:
        testdox_file = output_dir / "testdox.txt"
        if testdox_file.exists():
            try:
                testdox_content = testdox_file.read_text()
            except Exception:
                pass
    
    # Also check /workspace/testdox.txt (container path)
    workspace_testdox = Path("/workspace/testdox.txt")
    if workspace_testdox.exists():
        try:
            testdox_content = workspace_testdox.read_text()
        except Exception:
            pass
    
    # Parse testdox format from either file or stdout
    # Testdox format:
    # Abstract Csv (League\Csv\AbstractCsv)
    #  ✔ Create from file object preserve file object csv controls
    #  ✔ Create from path throws runtime exception
    #  ✘ Some failing test
    testdox_source = testdox_content if testdox_content else combined
    
    current_class = ""
    current_class_fqn = ""
    
    for line in testdox_source.splitlines():
        line = line.rstrip()
        if not line:
            continue
        
        # Check if this is a class name line
        # Format: "Abstract Csv (League\Csv\AbstractCsv)" or just "League\Csv\AbstractCsv"
        class_match = re.match(r'^([A-Z][^(]+)\s*\(([^)]+)\)\s*$', line)
        if class_match:
            current_class = class_match.group(1).strip()
            current_class_fqn = class_match.group(2).strip()
            continue
        
        # Also handle simple class name format without parentheses
        # Matches namespaced classes: "Namespace\ClassName" with "Test" in name
        # Or simple class names that look like class declarations (capitalized, no spaces)
        if not line.startswith(' ') and not line.startswith('\t'):
            stripped = line.strip()
            # Namespaced class with backslash
            if '\\' in stripped and 'Test' in stripped:
                current_class_fqn = stripped
                current_class = current_class_fqn.split('\\')[-1]
                continue
            # Simple class name (capitalized word, possibly ending in Test or just a class name)
            # Match: single word, starts with uppercase, no special chars except underscores
            if re.match(r'^[A-Z][a-zA-Z0-9_]*$', stripped) and len(stripped) > 2:
                current_class_fqn = stripped
                current_class = stripped
                continue
        
        # Check for passing test (✔ or [x] for older PHPUnit)
        match = re.match(r'^\s*(?:[✔✓☑]|\[x\])\s+(.+)$', line)
        if match:
            test_name = match.group(1).strip()
            if current_class_fqn:
                method_name = convert_testdox_to_method_name(test_name)
                passed.append(f"{current_class_fqn}::{method_name}")
            else:
                passed.append(test_name)
            continue
        
        # Check for failing test (✘ or [ ] for older PHPUnit failures)
        match = re.match(r'^\s*(?:[✘✗☒✕]|\[ \])\s+(.+)$', line)
        if match:
            test_name = match.group(1).strip()
            if current_class_fqn:
                method_name = convert_testdox_to_method_name(test_name)
                failed.append(f"{current_class_fqn}::{method_name}")
            else:
                failed.append(test_name)
            continue
        
        # Check for skipped test (⊘ or similar)
        match = re.match(r'^\s*(?:[⊘○◯]|\[-\])\s+(.+)$', line)
        if match:
            test_name = match.group(1).strip()
            if current_class_fqn:
                method_name = convert_testdox_to_method_name(test_name)
                skipped.append(f"{current_class_fqn}::{method_name}")
            else:
                skipped.append(test_name)
            continue
    
    if passed or failed:
        return passed, failed, skipped
    
    # Parse verbose format output
    # Format: "ReaderTest::testGetIterator ✔" or "ReaderTest::testGetIterator PASSED"
    for line in combined.splitlines():
        # PHPUnit 10+ verbose format
        match = re.search(r'^(\S+::\S+)\s+(?:✔|PASSED|passed)', line)
        if match:
            passed.append(match.group(1))
            continue
        
        match = re.search(r'^(\S+::\S+)\s+(?:✘|FAILED|failed)', line)
        if match:
            failed.append(match.group(1))
            continue
            
        match = re.search(r'^(\S+::\S+)\s+(?:⌛|SKIPPED|skipped)', line)
        if match:
            skipped.append(match.group(1))
            continue
    
    if passed or failed:
        return passed, failed, skipped
    
    # Fall back to parsing summary line and creating synthetic test names
    # This ensures we capture test counts even if individual names aren't available
    # Format: "Tests: 393, Assertions: 652" or "OK (393 tests, 652 assertions)"
    
    # Check for "OK" result (all passed)
    ok_match = re.search(r'OK\s*(?:\(|,)?\s*(\d+)\s+tests?', combined)
    if ok_match:
        test_count = int(ok_match.group(1))
        # Create synthetic test names based on count
        for i in range(test_count):
            passed.append(f"PHPUnit::test_{i+1}")
        return passed, failed, skipped
    
    # Check for "Tests: N, Assertions: M" with optional failures
    tests_match = re.search(r'Tests:\s*(\d+)', combined)
    failures_match = re.search(r'Failures:\s*(\d+)', combined)
    errors_match = re.search(r'Errors:\s*(\d+)', combined)
    skipped_match = re.search(r'Skipped:\s*(\d+)', combined)
    
    if tests_match:
        total_tests = int(tests_match.group(1))
        failures = int(failures_match.group(1)) if failures_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0
        skipped_count = int(skipped_match.group(1)) if skipped_match else 0
        
        # Calculate passed tests
        passed_count = total_tests - failures - errors - skipped_count
        
        # Create synthetic test names
        for i in range(passed_count):
            passed.append(f"PHPUnit::passed_test_{i+1}")
        for i in range(failures + errors):
            failed.append(f"PHPUnit::failed_test_{i+1}")
        for i in range(skipped_count):
            skipped.append(f"PHPUnit::skipped_test_{i+1}")
    
    # Also try to extract actual failure names from the output
    # PHPUnit shows failures like: "1) LeagueTest\ReaderTest::testMethod"
    failure_names = re.findall(r'^\d+\)\s+(\S+::\S+)', combined, re.MULTILINE)
    if failure_names:
        # Replace synthetic failures with real names
        failed = failure_names
    
    # Normalize test names to strip timing suffixes (e.g., "test name 0.01s" -> "test name")
    # This is especially important for Pest output which includes timing for slower tests
    passed = normalize_test_list(passed)
    failed = normalize_test_list(failed)
    skipped = normalize_test_list(skipped)
    
    return passed, failed, skipped


def _deduplicate_test_outcomes(
    passed: List[str], failed: List[str], skipped: List[str]
) -> Tuple[List[str], List[str], List[str]]:
    """
    Deduplicate test results when the same test appears multiple times with different outcomes.

    This happens in multi-module Maven/Gradle builds where the same integration test
    runs in different module contexts.

    Priority for conflicting outcomes:
    1. PASSED takes highest priority (if a test passed at least once, consider it passing)
    2. FAILED is next (if it failed but never passed, it's a real failure)
    3. SKIPPED is lowest (only if it was never run to completion)

    This ensures stable tests aren't incorrectly marked as failing due to
    environment issues in some module configurations.
    """
    # Collect all outcomes for each test
    test_outcomes: Dict[str, set] = {}

    for test in passed:
        test_outcomes.setdefault(test, set()).add('passed')
    for test in failed:
        test_outcomes.setdefault(test, set()).add('failed')
    for test in skipped:
        test_outcomes.setdefault(test, set()).add('skipped')

    # Resolve to single outcome per test
    final_passed = []
    final_failed = []
    final_skipped = []

    for test, outcomes in test_outcomes.items():
        if 'passed' in outcomes:
            # If it passed at least once, consider it passed
            final_passed.append(test)
        elif 'failed' in outcomes:
            # If it failed but never passed, it's a failure
            final_failed.append(test)
        else:
            # Only skipped
            final_skipped.append(test)

    return final_passed, final_failed, final_skipped


def parse_maven_output(stdout: str, stderr: str, repo_path: Path = None) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse Maven/Surefire test output to extract test results.

    Parses both console output and Surefire XML reports if available.
    Results are deduplicated to handle multi-module builds where the same
    test may run multiple times with different outcomes.

    Args:
        stdout: Standard output
        stderr: Standard error
        repo_path: Repository path (to find Surefire reports)

    Returns:
        Tuple of (passed, failed, skipped) test lists (deduplicated)
    """
    import re
    import glob
    import xml.etree.ElementTree as ET

    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # Try to parse JUnit XML reports first (most accurate)
    # Works for both Maven (Surefire) and Gradle test reports
    if repo_path:
        surefire_reports = []
        # Look for test reports in all modules - Maven and Gradle paths
        for pattern in [
            # Maven Surefire reports
            '**/target/surefire-reports/TEST-*.xml',
            '**/target/surefire-reports/*.xml',
            # Gradle test reports
            '**/build/test-results/test/TEST-*.xml',
            '**/build/test-results/*/TEST-*.xml',
            '**/build/test-results/**/TEST-*.xml',
            # Generic JUnit report locations
            '**/test-results/TEST-*.xml',
            '**/test-reports/TEST-*.xml',
        ]:
            surefire_reports.extend(glob.glob(str(repo_path / pattern), recursive=True))

        for report_file in surefire_reports:
            try:
                tree = ET.parse(report_file)
                root = tree.getroot()

                # Handle both testsuite and testsuites root elements
                testsuites = [root] if root.tag == 'testsuite' else root.findall('.//testsuite')

                for testsuite in testsuites:
                    suite_name = testsuite.get('name', '')
                    for testcase in testsuite.findall('testcase'):
                        class_name = testcase.get('classname', suite_name)
                        test_name = testcase.get('name', '')
                        full_name = f"{class_name}#{test_name}" if test_name else class_name

                        # Check for failure, error, or skipped
                        if testcase.find('failure') is not None or testcase.find('error') is not None:
                            failed.append(full_name)
                        elif testcase.find('skipped') is not None:
                            skipped.append(full_name)
                        else:
                            passed.append(full_name)
            except Exception as e:
                print(f"Warning: Failed to parse Surefire report {report_file}: {e}")
                continue

        if passed or failed or skipped:
            return _deduplicate_test_outcomes(passed, failed, skipped)

    # Fallback: Parse console output
    # Pattern: Tests run: X, Failures: Y, Errors: Z, Skipped: W
    for line in combined.splitlines():
        # Look for individual test results
        # [INFO] Running com.example.TestClass
        match = re.search(r'\[INFO\]\s+Running\s+(\S+)', line)
        if match:
            current_class = match.group(1)
            continue

        # Test method result (from verbose output)
        # testMethodName(com.example.TestClass)  Time elapsed: 0.001 sec  <<< FAILURE!
        match = re.search(r'(\w+)\(([^)]+)\).*<<<\s+(FAILURE|ERROR)', line)
        if match:
            method, class_name = match.group(1), match.group(2)
            failed.append(f"{class_name}#{method}")
            continue

        # Gradle test output format:
        # TestClass > test_method() PASSED
        # TestClass > test_method() FAILED
        # TestClass > test_method() SKIPPED
        match = re.search(r'^(\S+)\s+>\s+(\S+)\(\)\s+(PASSED|FAILED|SKIPPED)', line)
        if match:
            class_name, method_name, result = match.group(1), match.group(2), match.group(3)
            full_name = f"{class_name}#{method_name}"
            if result == "PASSED":
                passed.append(full_name)
            elif result == "FAILED":
                failed.append(full_name)
            elif result == "SKIPPED":
                skipped.append(full_name)
            continue

        # Summary line for a test class
        match = re.search(r'Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)', line)
        if match:
            # This gives us counts but not individual test names
            # We can't get names from summary, but we know the counts
            pass

    return _deduplicate_test_outcomes(passed, failed, skipped)


def parse_javascript_output(stdout: str, stderr: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse JavaScript test output (Jest, Mocha, Karma, Grunt/Jasmine).

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re

    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # Jest output patterns
    # PASS src/tests/example.test.js
    # FAIL src/tests/example.test.js
    for line in combined.splitlines():
        # Jest: ✓ test name (Xms)
        match = re.search(r'[✓✔]\s+(.+?)\s*\(\d+\s*m?s\)', line)
        if match:
            passed.append(match.group(1).strip())
            continue

        # Jest: ✕ test name (Xms)
        match = re.search(r'[✕✗×]\s+(.+?)\s*\(\d+\s*m?s\)', line)
        if match:
            failed.append(match.group(1).strip())
            continue

        # Jest: ○ skipped test name
        match = re.search(r'[○]\s+skipped\s+(.+)', line)
        if match:
            skipped.append(match.group(1).strip())
            continue

        # Mocha: ✓ test name
        match = re.search(r'^\s*[✓✔]\s+(.+)$', line)
        if match:
            passed.append(match.group(1).strip())
            continue

        # Mocha: X) test name (failure)
        match = re.search(r'^\s*\d+\)\s+(.+)$', line)
        if match and 'passing' not in line.lower() and 'failing' not in line.lower():
            failed.append(match.group(1).strip())
            continue

        # Karma/Jasmine: Executed X of Y (Z FAILED)
        match = re.search(r'Executed\s+(\d+)\s+of\s+(\d+).*?(\d+)\s+FAILED', line)
        if match:
            total, _, fail_count = int(match.group(1)), int(match.group(2)), int(match.group(3))
            # Can't get individual names, but we know counts
            pass

        # Karma/Jasmine: FAILED - X specs, Y failures
        match = re.search(r'(\d+)\s+specs?,\s*(\d+)\s+failures?', line)
        if match:
            pass

    # Normalize test names to strip timing suffixes (e.g., "test name 0.01s" -> "test name")
    # This prevents the same test from appearing as different tests due to timing variations
    passed = normalize_test_list(passed)
    failed = normalize_test_list(failed)
    skipped = normalize_test_list(skipped)

    return passed, failed, skipped


def parse_rust_output(stdout: str, stderr: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse Rust/Cargo test output to extract test results.

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re

    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # cargo test output format:
    # test module::test_name ... ok
    # test module::test_name ... FAILED
    # test module::test_name ... ignored
    for line in combined.splitlines():
        # Passing test
        match = re.search(r'^test\s+(\S+)\s+\.\.\.\s+ok', line)
        if match:
            passed.append(match.group(1))
            continue

        # Failing test
        match = re.search(r'^test\s+(\S+)\s+\.\.\.\s+FAILED', line)
        if match:
            failed.append(match.group(1))
            continue

        # Ignored/skipped test
        match = re.search(r'^test\s+(\S+)\s+\.\.\.\s+ignored', line)
        if match:
            skipped.append(match.group(1))
            continue

    return passed, failed, skipped


def parse_ruby_minitest_output(stdout: str, stderr: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse Ruby Minitest/Test::Unit output to extract test results.

    Minitest verbose output format (with -v flag):
    ```
    Run options: -v --seed 12345

    # Running:

    TestClass#test_something = 0.01 s = .
    TestClass#test_other = 0.02 s = F
    TestClass#test_skipped = 0.00 s = S

    Finished in 1.234567s, 100.0000 runs/s, 200.0000 assertions/s.

      1) Failure:
    TestClass#test_other [/path/to/test.rb:50]:
    Expected: true
      Actual: false

    100 runs, 200 assertions, 1 failures, 0 errors, 1 skips
    ```

    Also handles the format with nested test classes:
    ```
    SnippetTest::LaxMode#test_valid_inline_snippet = 0.01 s = .
    ```

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re

    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # First, try to parse verbose output format
    # Pattern: "TestClass#test_method = X.XX s = ." (or F, E, S)
    # Also handles: "TestClass::NestedClass#test_method = X.XX s = ."
    verbose_pattern = r'^([\w:]+#[\w]+)\s+=\s+[\d.]+\s+s\s+=\s+([.FES])'

    for match in re.finditer(verbose_pattern, combined, re.MULTILINE):
        test_name = match.group(1)
        result = match.group(2)

        if result == '.':
            if test_name not in passed:
                passed.append(test_name)
        elif result == 'F' or result == 'E':
            if test_name not in failed:
                failed.append(test_name)
        elif result == 'S':
            if test_name not in skipped:
                skipped.append(test_name)

    # If verbose parsing worked, return those results
    if passed or failed or skipped:
        return passed, failed, skipped

    # Fall back to non-verbose parsing (summary-based)
    # Parse test summary line: "100 runs, 200 assertions, 1 failures, 1 errors, 2 skips"
    summary_pattern = r'(\d+)\s+runs?,\s+(\d+)\s+assertions?,\s+(\d+)\s+failures?,\s+(\d+)\s+errors?,\s+(\d+)\s+skips?'

    total_runs = 0
    total_failures = 0
    total_errors = 0
    total_skips = 0

    # Sum up all runs (Minitest can run multiple times in rake task)
    for match in re.finditer(summary_pattern, combined):
        runs = int(match.group(1))
        failures = int(match.group(3))
        errors = int(match.group(4))
        skips = int(match.group(5))

        total_runs += runs
        total_failures += failures
        total_errors += errors
        total_skips += skips

    # Parse individual failure/error names from output
    # Format: "1) Failure:\nTestClass#test_method [file:line]:"
    failure_pattern = r'^\s*\d+\)\s+(?:Failure|Error):\s*\n\s*(\S+#\S+)'
    for match in re.finditer(failure_pattern, combined, re.MULTILINE):
        test_name = match.group(1)
        if test_name not in failed:
            failed.append(test_name)

    # Also try single-line format: "Failure: TestClass#test_method"
    single_failure_pattern = r'(?:Failure|Error):\s*(\w+#\w+)'
    for match in re.finditer(single_failure_pattern, combined):
        test_name = match.group(1)
        if test_name not in failed:
            failed.append(test_name)

    # Parse skipped tests
    # Format: "3) Skipped:\nTestClass#test_method [file:line]:"
    skip_pattern = r'^\s*\d+\)\s+Skipped:\s*\n\s*(\S+#\S+)'
    for match in re.finditer(skip_pattern, combined, re.MULTILINE):
        test_name = match.group(1)
        if test_name not in skipped:
            skipped.append(test_name)

    # If we found summary but no individual names, generate synthetic test entries
    # This happens when tests pass (no detailed output) or output is truncated
    if total_runs > 0:
        # Calculate passed = total_runs - failures - errors - skips
        calculated_passed = total_runs - total_failures - total_errors - total_skips

        # If we don't have individual test names, generate summary entries
        if not passed and not failed and not skipped:
            # Generate synthetic passed tests
            for i in range(calculated_passed):
                passed.append(f"test_{i+1}")

            # Generate synthetic failed tests (failures + errors)
            for i in range(total_failures + total_errors):
                failed.append(f"failed_test_{i+1}")

            # Generate synthetic skipped tests
            for i in range(total_skips):
                skipped.append(f"skipped_test_{i+1}")
        else:
            # We have some individual names, fill in the rest
            num_passed = calculated_passed - len(passed)
            if num_passed > 0:
                for i in range(num_passed):
                    passed.append(f"test_{i+1}")

            num_failed = (total_failures + total_errors) - len(failed)
            if num_failed > 0:
                for i in range(num_failed):
                    failed.append(f"failed_test_{len(failed)+i+1}")

            num_skipped = total_skips - len(skipped)
            if num_skipped > 0:
                for i in range(num_skipped):
                    skipped.append(f"skipped_test_{len(skipped)+i+1}")

    return passed, failed, skipped


def parse_ruby_rspec_output(stdout: str, stderr: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse Ruby RSpec output to extract test results.

    RSpec output format (documentation format):
    ```
    MyClass
      #method
        does something (FAILED - 1)
        does another thing
        pending example (PENDING: reason)

    Failures:
      1) MyClass#method does something
         Failure/Error: expect(true).to eq(false)
         ...

    Finished in 1.23 seconds (files took 0.5 seconds to load)
    10 examples, 2 failures, 1 pending
    ```

    Args:
        stdout: Standard output
        stderr: Standard error

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re

    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # Parse summary line: "10 examples, 2 failures" or "10 examples, 2 failures, 1 pending"
    summary_pattern = r'(\d+)\s+examples?,\s+(\d+)\s+failures?(?:,\s+(\d+)\s+pending)?'

    total_examples = 0
    total_failures = 0
    total_pending = 0

    for match in re.finditer(summary_pattern, combined):
        total_examples += int(match.group(1))
        total_failures += int(match.group(2))
        if match.group(3):
            total_pending += int(match.group(3))

    # Parse individual failure descriptions
    # Format in Failures section: "1) ClassName#method description"
    failures_section = re.search(r'Failures?:(.*?)(?=Finished|$)', combined, re.DOTALL)
    if failures_section:
        failure_pattern = r'^\s*\d+\)\s+(.+?)$'
        for match in re.finditer(failure_pattern, failures_section.group(1), re.MULTILINE):
            test_name = match.group(1).strip()
            # Clean up test name (remove trailing failure info)
            test_name = re.sub(r'\s+\(FAILED.*\)$', '', test_name)
            if test_name and test_name not in failed:
                failed.append(test_name)

    # Parse pending examples
    pending_section = re.search(r'Pending:(.*?)(?=Failures?|Finished|$)', combined, re.DOTALL)
    if pending_section:
        pending_pattern = r'^\s*\d+\)\s+(.+?)$'
        for match in re.finditer(pending_pattern, pending_section.group(1), re.MULTILINE):
            test_name = match.group(1).strip()
            if test_name and test_name not in skipped:
                skipped.append(test_name)

    # Generate synthetic entries for remaining tests
    if total_examples > 0:
        calculated_passed = total_examples - total_failures - total_pending

        if not passed and not failed and not skipped:
            for i in range(calculated_passed):
                passed.append(f"example_{i+1}")
            for i in range(total_failures):
                failed.append(f"failed_example_{i+1}")
            for i in range(total_pending):
                skipped.append(f"pending_example_{i+1}")
        else:
            num_passed = calculated_passed - len(passed)
            if num_passed > 0:
                for i in range(num_passed):
                    passed.append(f"example_{i+1}")

    return passed, failed, skipped


def parse_dotnet_output(stdout: str, stderr: str, repo_path: Path = None) -> Tuple[List[str], List[str], List[str]]:
    """
    Parse .NET/dotnet test output to extract test results.

    Supports:
    - dotnet test console output
    - TRX report files

    Args:
        stdout: Standard output
        stderr: Standard error
        repo_path: Repository path (to find TRX reports)

    Returns:
        Tuple of (passed, failed, skipped) test lists
    """
    import re
    import glob
    import xml.etree.ElementTree as ET

    passed = []
    failed = []
    skipped = []

    combined = stdout + stderr

    # Try to parse TRX files first (most accurate)
    if repo_path:
        trx_files = glob.glob(str(repo_path / "**/TestResults/*.trx"), recursive=True)
        for trx_file in trx_files:
            try:
                tree = ET.parse(trx_file)
                root = tree.getroot()
                # TRX namespace
                ns = {'t': 'http://microsoft.com/schemas/VisualStudio/TeamTest/2010'}
                
                for result in root.findall('.//t:UnitTestResult', ns):
                    test_name = result.get('testName', 'unknown')
                    outcome = result.get('outcome', '').lower()
                    
                    if outcome == 'passed':
                        passed.append(test_name)
                    elif outcome == 'failed':
                        failed.append(test_name)
                    elif outcome in ('skipped', 'notexecuted', 'inconclusive'):
                        skipped.append(test_name)
            except Exception as e:
                print(f"Warning: Failed to parse TRX file {trx_file}: {e}")
                continue

        if passed or failed or skipped:
            return passed, failed, skipped

    # Fallback: Parse console output
    # dotnet test output format:
    # Passed  TestNamespace.TestClass.TestMethod [< 1 ms]
    # Failed  TestNamespace.TestClass.TestMethod [1 ms]
    # Skipped TestNamespace.TestClass.TestMethod [< 1 ms]
    for line in combined.splitlines():
        # Passed test
        match = re.search(r'^\s*Passed\s+(\S+)', line)
        if match:
            passed.append(match.group(1))
            continue

        # Failed test  
        match = re.search(r'^\s*Failed\s+(\S+)', line)
        if match:
            failed.append(match.group(1))
            continue

        # Skipped test
        match = re.search(r'^\s*Skipped\s+(\S+)', line)
        if match:
            skipped.append(match.group(1))
            continue

    # Also try to parse summary line for totals
    # Total: X, Passed: Y, Failed: Z, Skipped: W
    summary_match = re.search(r'Passed:\s*(\d+).*?Failed:\s*(\d+).*?Skipped:\s*(\d+)', combined)
    if summary_match and not (passed or failed):
        # We found a summary but no individual test names
        # This happens with less verbose output
        pass

    return passed, failed, skipped


def run_tests(
    test_command: str,
    repo_path: Path,
    output_dir: Path
) -> Dict:
    """
    Run tests and collect results.

    Args:
        test_command: Test command to execute
        repo_path: Repository path
        output_dir: Output directory for results

    Returns:
        Test result dictionary
    """
    print(f"Running tests: {test_command}")
    print(f"Working directory: {repo_path}")

    start_time = time.time()

    # Parse command - use shlex to handle quoted strings properly
    import shlex
    try:
        cmd_parts = shlex.split(test_command)
    except ValueError:
        # Fallback to simple split if shlex fails
        cmd_parts = test_command.split()

    # Add flags for different test frameworks
    if "pytest" in test_command:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_report = output_dir / "pytest_report.json"
        cmd_parts.extend([
            "--json-report",
            f"--json-report-file={json_report}",
            "-v",
            # Ignore common non-test directories that might contain files with "test" in the name
            "--ignore=automation_script",
            "--ignore=node_modules",
            "--ignore=.git",
            "--ignore=venv",
            "--ignore=.venv",
            "--ignore=build",
            "--ignore=dist",
        ])
    elif cmd_parts and cmd_parts[0] == "go":
        # Add -json flag for structured output (Go 1.13+)
        # Note: We check cmd_parts[0] == "go" instead of "go test" in test_command
        # because "cargo test" contains "go test" as a substring!
        if "-json" not in cmd_parts:
            cmd_parts.append("-json")
    elif "gradle" in test_command.lower() or "gradlew" in test_command.lower():
        # Add flags for verbose test output and continue on failure
        # --info shows all test results (not just failures)
        # --continue runs all tests even if some fail
        # --no-daemon avoids daemon issues in containers
        if "--info" not in cmd_parts:
            cmd_parts.append("--info")
        if "--continue" not in cmd_parts:
            cmd_parts.append("--continue")
        if "--no-daemon" not in cmd_parts:
            cmd_parts.append("--no-daemon")
    elif "mvn" in test_command.lower():
        # Maven: add fail-at-end to run all tests
        if "-fae" not in cmd_parts and "--fail-at-end" not in cmd_parts:
            cmd_parts.append("-fae")
    elif any(x in test_command.lower() for x in ["rake test", "bundle exec rake", "ruby -itest", "minitest"]):
        # Ruby Minitest: add verbose flag to get individual test names
        # Without -v, we only get summary counts and can't extract test names
        if "-v" not in cmd_parts and "--verbose" not in cmd_parts:
            # For rake, use -- to pass args to minitest
            if "rake" in test_command.lower():
                # Check if TESTOPTS is already set
                if "TESTOPTS" not in test_command:
                    cmd_parts.extend(["TESTOPTS=-v"])
            else:
                cmd_parts.append("-v")

    # Handle Node.js local binaries - use npx if command is a local binary
    # This handles cases where grunt/gulp/etc are installed locally in node_modules/.bin/
    if cmd_parts and cmd_parts[0] in ("grunt", "gulp", "karma", "mocha", "jest"):
        # Prepend npx to use locally installed binary
        cmd_parts = ["npx"] + cmd_parts

    # Execute tests with longer timeout for large test suites
    timeout = 1800  # 30 minutes (increased from 600s/10min)
    exit_code, stdout, stderr = run_command(
        cmd_parts,
        cwd=repo_path,
        timeout=timeout
    )

    duration = time.time() - start_time

    print(f"Tests completed in {duration:.1f}s with exit code {exit_code}")

    # Parse results
    passed = []
    failed = []
    skipped = []

    # Try to parse from pytest JSON report
    if "pytest" in test_command:
        json_report = output_dir / "pytest_report.json"
        if json_report.exists():
            try:
                with open(json_report) as f:
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
            except:
                pass

    # Fallback to parsing output
    if not passed and not failed:
        # Detect test framework and use appropriate parser
        test_cmd_lower = test_command.lower()

        if "pytest" in test_cmd_lower:
            passed, failed, skipped = parse_pytest_output(stdout, stderr)
        elif "cargo test" in test_cmd_lower:
            # Rust tests - check before "go test" because "cargo test" contains "go test" as substring!
            passed, failed, skipped = parse_rust_output(stdout, stderr)
        elif "go test" in test_cmd_lower:
            passed, failed, skipped = parse_go_test_output(stdout, stderr)
        elif "mvn" in test_cmd_lower or "maven" in test_cmd_lower:
            # Maven/Surefire tests
            passed, failed, skipped = parse_maven_output(stdout, stderr, repo_path)
        elif "dotnet test" in test_cmd_lower or "dotnet" in test_cmd_lower:
            # .NET tests
            passed, failed, skipped = parse_dotnet_output(stdout, stderr, repo_path)
        elif any(x in test_cmd_lower for x in ["rspec", "bundle exec rspec"]):
            # Ruby RSpec tests
            passed, failed, skipped = parse_ruby_rspec_output(stdout, stderr)
        elif any(x in test_cmd_lower for x in ["rake test", "bundle exec rake", "ruby -itest", "minitest"]):
            # Ruby Minitest tests
            passed, failed, skipped = parse_ruby_minitest_output(stdout, stderr)
        elif any(x in test_cmd_lower for x in ["npm test", "yarn test", "jest", "mocha", "karma", "grunt"]):
            # JavaScript tests (Jest, Mocha, Karma, Grunt/Jasmine)
            passed, failed, skipped = parse_javascript_output(stdout, stderr)
        elif any(x in test_cmd_lower for x in ["phpunit", "vendor/bin/phpunit", "composer test"]):
            # PHP tests (PHPUnit)
            passed, failed, skipped = parse_phpunit_output(stdout, stderr, test_command)
        elif "gradle" in test_cmd_lower:
            # Gradle uses similar format to Maven for test output
            passed, failed, skipped = parse_maven_output(stdout, stderr, repo_path)
        else:
            # Try all parsers and use the one that finds results
            print(f"Unknown test framework, trying all parsers for: {test_command}")

            # Try Maven parser (works for many JVM languages)
            passed, failed, skipped = parse_maven_output(stdout, stderr, repo_path)

            # If no results, try JavaScript
            if not passed and not failed:
                passed, failed, skipped = parse_javascript_output(stdout, stderr)

            # If still no results, try pytest (might catch generic test output)
            if not passed and not failed:
                passed, failed, skipped = parse_pytest_output(stdout, stderr)

            # If still no results, try PHP/PHPUnit
            if not passed and not failed:
                passed, failed, skipped = parse_phpunit_output(stdout, stderr, test_command)

            # If still no results, try Ruby Minitest
            if not passed and not failed:
                passed, failed, skipped = parse_ruby_minitest_output(stdout, stderr)

            # If still no results, try Ruby RSpec
            if not passed and not failed:
                passed, failed, skipped = parse_ruby_rspec_output(stdout, stderr)

    # Build result
    result = {
        "success": exit_code == 0,
        "exit_code": exit_code,
        "stdout": stdout if exit_code != 0 else stdout[:1000],  # Limit stdout for passing tests
        "stderr": stderr,
        "duration": round(duration, 2),
        "tests_passed": passed,
        "tests_failed": failed,
        "tests_skipped": skipped,
        "error_type": None
    }

    # Detect environment errors (comprehensive detection for all languages)
    combined = stdout + stderr
    combined_lower = combined.lower()

    # Python errors
    if "ModuleNotFoundError" in combined or "No module named" in combined:
        result["error_type"] = "missing_module"
    elif "ImportError" in combined:
        result["error_type"] = "import_error"

    # Java/Maven/Gradle errors
    elif "UnsupportedClassVersionError" in combined:
        result["error_type"] = "java_version_error"
    elif "class file version" in combined_lower and "java runtime only recognizes" in combined_lower:
        result["error_type"] = "java_version_error"
    elif "invalid source release" in combined_lower or "invalid target release" in combined_lower:
        result["error_type"] = "java_version_error"
    elif "source option" in combined_lower and "no longer supported" in combined_lower:
        result["error_type"] = "java_version_error"
    elif "Java compilation initialization error" in combined:
        result["error_type"] = "java_compilation_error"
    elif "Execution failed for task" in combined and "compileJava" in combined:
        result["error_type"] = "java_compilation_error"
    elif "BUILD FAILURE" in combined and ("maven" in combined_lower or "mvn" in combined_lower):
        if "Failed to execute goal" in combined:
            if any(x in combined_lower for x in ["checkstyle", "spotbugs", "pmd", "findbugs"]):
                result["error_type"] = "maven_plugin_error"
            else:
                result["error_type"] = "maven_build_error"
    elif "Could not find artifact" in combined or "Cannot resolve dependencies" in combined:
        result["error_type"] = "maven_dependency_error"

    # Node.js/JavaScript errors
    elif "npm ERR!" in combined:
        if "ENOENT" in combined:
            result["error_type"] = "npm_missing_file"
        elif "network" in combined_lower:
            result["error_type"] = "npm_network_error"
        else:
            result["error_type"] = "npm_error"
    elif 'The engine "node" is incompatible' in combined:
        result["error_type"] = "node_version_error"

    # Go errors
    elif "panic:" in combined:
        result["error_type"] = "go_panic"
    elif "cannot find package" in combined_lower:
        result["error_type"] = "go_missing_package"

    # Rust errors
    elif "error[E" in combined:  # Rust compiler errors have format error[E0XXX]
        result["error_type"] = "rust_compile_error"

    # Generic errors
    elif "command not found" in combined_lower:
        result["error_type"] = "missing_command"
    elif "permission denied" in combined_lower:
        result["error_type"] = "permission_error"
    elif "out of memory" in combined_lower or "MemoryError" in combined:
        result["error_type"] = "memory_error"
    # Timeout detection - use specific patterns to avoid false positives from test names
    # containing "timeout" (e.g., "test_client_http_config_negative_timeout")
    elif exit_code == 124:
        result["error_type"] = "timeout_error"
    else:
        timeout_indicators = [
            'timed out', 'operation timed out', 'connection timed out',
            'test timed out', 'execution timed out', 'deadline exceeded',
            f'command timed out after {timeout}s'  # Our own timeout message
        ]
        if any(indicator in combined_lower for indicator in timeout_indicators):
            result["error_type"] = "timeout_error"

    print(f"Results: {len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")

    return result


def save_result(result: Dict, output_dir: Path) -> None:
    """
    Save test result to output directory.

    Args:
        result: Test result dictionary
        output_dir: Output directory
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    result_file = output_dir / "result.json"
    with open(result_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Result saved to {result_file}")

    # Also save JSONL
    jsonl_file = output_dir / "results.jsonl"
    with open(jsonl_file, "w") as f:
        for test in result["tests_passed"]:
            f.write(json.dumps({"test": test, "outcome": "passed"}) + "\n")
        for test in result["tests_failed"]:
            f.write(json.dumps({"test": test, "outcome": "failed"}) + "\n")
        for test in result["tests_skipped"]:
            f.write(json.dumps({"test": test, "outcome": "skipped"}) + "\n")


def main():
    """Main entry point for container runner."""
    parser = argparse.ArgumentParser(description="Container-side test runner")

    parser.add_argument(
        "--mode",
        choices=["base", "patched"],
        required=True,
        help="Run mode: base or patched"
    )

    parser.add_argument(
        "--test-command",
        required=True,
        help="Test command to execute"
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for results"
    )

    parser.add_argument(
        "--patch",
        type=Path,
        help="Patch file to apply (required for patched mode)"
    )

    parser.add_argument(
        "--repo-path",
        type=Path,
        default=None,
        help="Repository path (default /repo). If /mnt/repo, node_modules are copied from image /repo first."
    )

    args = parser.parse_args()

    print(f"=== Container Runner: {args.mode.upper()} mode ===")

    # Default repo path is /app/repo (new standardized structure)
    repo_path = args.repo_path if args.repo_path is not None else Path("/app/repo")
    print(f"Repository path: {repo_path}")

    # Handle patched mode
    if args.mode == "patched":
        if not args.patch:
            print("ERROR: --patch required for patched mode", file=sys.stderr)
            sys.exit(1)

        success, error = apply_patch(args.patch, repo_path)
        if not success:
            # Save error result
            result = {
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Patch application failed: {error}",
                "duration": 0,
                "tests_passed": [],
                "tests_failed": [],
                "tests_skipped": [],
                "error_type": "patch_failed"
            }
            save_result(result, args.output)
            sys.exit(1)

        # Post-patch hooks: regenerate autoloader for PHP projects
        # This is needed when the patch adds new namespaced classes
        if (repo_path / "composer.json").exists():
            print("Regenerating PHP autoloader after patch...")
            try:
                subprocess.run(
                    ["composer", "dump-autoload", "--no-interaction"],
                    cwd=repo_path,
                    capture_output=True,
                    timeout=60
                )
            except Exception as e:
                print(f"Warning: composer dump-autoload failed: {e}")

    # Run tests
    result = run_tests(args.test_command, repo_path, args.output)

    # Save result
    save_result(result, args.output)

    # Exit with test exit code
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
