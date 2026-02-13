"""
Simplified metadata generation for two-part workflow.
"""

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import List, Optional

from .github_api import get_problem_statement


def clean_java_test_name(test_name: str) -> str:
    """
    Clean Java test name to format: package.ClassName.methodName
    
    Input formats:
    - package.Class#methodName()
    - package.Class#[N] methodName[params]
    - package.Class#displayName : description
    - package.Class$InnerClass#methodName
    
    Output: package.ClassName.methodName (clean identifier only)
    
    Rules:
    - No quotes around the name
    - No parameter info [N] or [param=value]
    - No descriptions after : or -
    - No parentheses ()
    - No commas or special characters in method name
    """
    # Split on # to separate class from method
    if '#' in test_name:
        class_part, method_part = test_name.split('#', 1)
    else:
        # Already in dot format or no method separator
        # Clean up any remaining artifacts
        return re.sub(r'\(\)$', '', test_name).strip()
    
    # Clean method part:
    # 1. Remove [N] prefix (parameterized test index)
    method_part = re.sub(r'^\[\d+\]\s*', '', method_part)
    
    # 2. Check if this is a regular method name (ends with () or is camelCase identifier)
    # Regular method pattern: camelCase or snake_case followed by optional ()
    regular_method_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\(\)?$', method_part)
    if regular_method_match:
        # This is a regular method call like "testMethod()" or "testMethod"
        method_name = regular_method_match.group(1)
    else:
        # This might be a display name or parameterized test name
        # Strategy: Extract only the first valid Java identifier
        
        # a. First, strip parameter blocks [...]
        method_part = re.sub(r'\[.*?\]', '', method_part)
        method_part = re.sub(r'\[.*$', '', method_part)  # Incomplete brackets
        
        # b. Remove () at end
        method_part = re.sub(r'\(\)$', '', method_part)
        
        # c. Remove descriptions after " : " or ":"
        if ' : ' in method_part:
            method_part = method_part.split(' : ')[0]
        elif ':' in method_part:
            method_part = method_part.split(':')[0]
        
        # d. For display names with spaces (like "Different strings produce..."),
        #    take only the first valid Java identifier
        #    This gives us "Different" which is the test method name
        first_word_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_-]*)', method_part.strip())
        if first_word_match:
            method_name = first_word_match.group(1)
            # Clean up any trailing hyphen (from "non-normal" -> "non-normal")
            method_name = method_name.rstrip('-')
        else:
            # Fallback: sanitize the whole thing
            method_name = re.sub(r'[^a-zA-Z0-9_]', '_', method_part)
            method_name = re.sub(r'_+', '_', method_name).strip('_')
        
        # e. If still empty or invalid, use a fallback
        if not method_name or not re.match(r'^[a-zA-Z_]', method_name):
            method_name = 'test'
    
    # Combine: package.ClassName.methodName
    return f"{class_part}.{method_name}"


def convert_tests_to_standard_format(test_names: List[str], repo: str, language: str) -> List[str]:
    """
    Convert test names to format expected by test log parsers.
    
    Formatting Rules (Strict):
    - Output is a flat list of clean test identifier strings
    - No status words (like PASSED/FAILED), dates, or extra descriptions
    - Just the test identifiers
    
    Naming Conventions per Language:
    
    Python (Pytest):
        folder/file.py::ClassName::method_name[parameter]
    
    Java (JUnit):
        package.ClassName.methodName
    
    Rust (Cargo):
        crate::module::path::test_name
    
    Go (Testing):
        package/path/TestName
    
    C++ (GTest):
        TestSuiteName.TestName
    
    JavaScript (Jest/Mocha):
        describeBlockName testName
    
    Constraints:
    - No ellipses (...) or truncation
    - No internal commas within test name strings
    - No JSON escaping artifacts
    
    Args:
        test_names: List of test names from test runner output
        repo: Repository in format "owner/repo"
        language: Programming language
        
    Returns:
        List of test names matching parser expected format
    """
    if not test_names:
        return test_names
    
    formatted_tests = []
    
    for test_name in test_names:
        formatted_name = test_name
        
        if language.lower() == "python":
            # Python pytest format: folder/file.py::ClassName::method_name[parameter]
            # Keep as-is if already in correct format
            if '::' in test_name:
                formatted_name = test_name
            elif '.' in test_name:
                # Convert module.Class.method to path/module.py::Class::method
                parts = test_name.split('.')
                if len(parts) >= 3:
                    # module.submodule.Class.method
                    module_path = '/'.join(parts[:-2])
                    class_name = parts[-2]
                    method_name = parts[-1]
                    formatted_name = f"{module_path}.py::{class_name}::{method_name}"
                elif len(parts) == 2:
                    formatted_name = f"{parts[0]}.py::{parts[1]}"
                else:
                    formatted_name = test_name
        
        elif language.lower() == "go":
            # Go format: package/path/TestName (slash-separated)
            
            # Strip repo prefix (github.com/owner/repo/)
            prefix_pattern = rf'^github\.com/{re.escape(repo)}/'
            relative_name = re.sub(prefix_pattern, '', test_name)
            for host in ['gitlab.com', 'bitbucket.org', 'gitee.com']:
                alt_prefix = rf'^{re.escape(host)}/{re.escape(repo)}/'
                relative_name = re.sub(alt_prefix, '', relative_name)
            
            # Keep slash-separated format for Go
            # pkg.TestName -> pkg/TestName
            if '.' in relative_name and '/' not in relative_name:
                parts = relative_name.rsplit('.', 1)
                if len(parts) == 2:
                    formatted_name = f"{parts[0]}/{parts[1]}"
                else:
                    formatted_name = relative_name
            else:
                formatted_name = relative_name
        
        elif language.lower() == "java":
            # Java format: package.ClassName.methodName
            # Clean format: no quotes, no parameters, no descriptions
            formatted_name = clean_java_test_name(test_name)
        
        elif language.lower() == "rust":
            # Rust format: crate::module::path::test_name (double-colon separated)
            # Already uses :: separator, keep as-is
            formatted_name = test_name
        
        elif language.lower() in ("javascript", "typescript"):
            # Jest/Mocha format: describeBlockName testName
            # Keep as-is, the format is already correct for Jest/Mocha
            if ' > ' in test_name:
                # Nested describe blocks: "describe > nested > test" -> "describe nested test"
                formatted_name = test_name.replace(' > ', ' ')
            else:
                formatted_name = test_name
        
        elif language.lower() in ("cpp", "c++"):
            # C++ GTest format: TestSuiteName.TestName
            # Already dot-separated, keep as-is
            formatted_name = test_name
        
        elif language.lower() == "csharp":
            # C# format: Namespace.ClassName.TestMethod (dot-separated)
            formatted_name = test_name
        
        elif language.lower() == "ruby":
            # Ruby RSpec format: description string
            formatted_name = test_name
        
        else:
            # Generic: keep as-is
            formatted_name = test_name
        
        formatted_tests.append(formatted_name)
    
    return formatted_tests


# Backward compatibility alias
def convert_tests_to_relative_paths(test_names: List[str], repo: str, language: str) -> List[str]:
    """Alias for convert_tests_to_standard_format for backward compatibility."""
    return convert_tests_to_standard_format(test_names, repo, language)


def generate_instance_id() -> str:
    """
    Generate a 16-digit unique ID using timestamp.
    Format: timestamp in milliseconds (13 digits) + random 3 digits
    """
    timestamp_ms = int(time.time() * 1000)  # 13 digits
    random_suffix = random.randint(100, 999)  # 3 digits
    return f"{timestamp_ms}{random_suffix}"


def determine_test_output_parser(test_command: str, language: str = "") -> str:
    """
    Determine test output parser based on test command and language.
    
    Parser names match those in the evaluation registry:
    - python/parse_log_pytest_v3 (Python pytest)
    - python/parse_log_unittest (Python unittest)
    - java/parse_log_junit (Java Maven/Gradle/JUnit)
    - rust/parse_log_cargo_test (Rust cargo)
    - go/parse_log_go_test (Go)
    - c/parse_log_meson, cpp/parse_log_meson (C/C++ meson)
    - generic/parse_log_make (fallback)
    
    Args:
        test_command: The test command string
        language: Programming language (optional, helps with detection)
    
    Returns:
        Parser name string matching evaluation registry
    """
    test_command_lower = test_command.lower()
    language_lower = language.lower() if language else ""
    
    # Python parsers
    if "pytest" in test_command_lower:
        return "python/parse_log_pytest_v3"
    elif "unittest" in test_command_lower or "python -m unittest" in test_command_lower:
        return "python/parse_log_unittest"
    
    # Java/JVM parsers (Maven, Gradle, Ant with JUnit)
    elif "mvn" in test_command_lower or "maven" in test_command_lower:
        return "java/parse_log_junit"
    elif "gradle" in test_command_lower or "gradlew" in test_command_lower:
        return "java/parse_log_junit"
    elif "ant" in test_command_lower and "test" in test_command_lower:
        return "java/parse_log_junit"
    
    # PHP parsers (PHPUnit uses JUnit-style output)
    elif "phpunit" in test_command_lower or "vendor/bin/phpunit" in test_command_lower:
        return "php/parse_log_phpunit"
    elif "composer test" in test_command_lower:
        return "php/parse_log_phpunit"
    
    # JavaScript/TypeScript parsers
    elif "jest" in test_command_lower:
        return "javascript/parse_log_jest"
    elif "mocha" in test_command_lower:
        return "javascript/parse_log_mocha"
    elif "npm test" in test_command_lower or "yarn test" in test_command_lower:
        # npm/yarn test could be anything, try to use language hint
        if language_lower in ("javascript", "typescript", "js", "ts"):
            return "javascript/parse_log_jest"  # Most common
        return "generic/parse_log_make"
    
    # Rust parser (check before Go since "cargo test" contains "go test")
    elif "cargo test" in test_command_lower or "cargo-test" in test_command_lower:
        return "rust/parse_log_cargo_test"
    
    # Go parser
    elif "go test" in test_command_lower:
        return "go/parse_log_go_test"
    
    # C/C++ parsers
    elif "meson test" in test_command_lower or "ninja test" in test_command_lower:
        if language_lower == "cpp" or language_lower == "c++":
            return "cpp/parse_log_meson"
        return "c/parse_log_meson"
    elif "ctest" in test_command_lower or "cmake" in test_command_lower:
        return "cpp/parse_log_meson"  # Similar enough format
    elif "make test" in test_command_lower or "make check" in test_command_lower:
        return "generic/parse_log_make"
    
    # Language-based fallbacks when command doesn't match
    elif language_lower == "java" or language_lower == "kotlin" or language_lower == "scala":
        return "java/parse_log_junit"
    elif language_lower == "python":
        return "python/parse_log_pytest_v3"
    elif language_lower in ("javascript", "typescript", "js", "ts"):
        return "javascript/parse_log_jest"
    elif language_lower == "go" or language_lower == "golang":
        return "go/parse_log_go_test"
    elif language_lower == "rust":
        return "rust/parse_log_cargo_test"
    elif language_lower in ("c", "cpp", "c++"):
        return "c/parse_log_meson"
    elif language_lower == "php":
        return "php/parse_log_phpunit"
    
    # Default fallback
    else:
        return "generic/parse_log_make"


def generate_metadata(
    repo: str,
    base_commit: str,
    pr_commit: str,
    language: str,
    test_command: str,
    fail_to_pass: List[str],
    pass_to_pass: List[str],
    image_uri: str,
    patch_file: Path,
    repo_path: Path,
    metadata_dir: Path,
    logger: logging.Logger,
    pr_number: Optional[int] = None,
    pr_url: Optional[str] = None
) -> Optional[dict]:
    """
    Generate metadata.json with all required fields.

    Args:
        repo: Repository in format "owner/repo"
        base_commit: BASE commit SHA
        pr_commit: PR commit SHA
        language: Repository language
        test_command: Test command used
        fail_to_pass: List of FAIL_TO_PASS test names
        pass_to_pass: List of PASS_TO_PASS test names
        image_uri: Docker image URI
        patch_file: Path to patch file
        repo_path: Path to repository
        metadata_dir: Directory to save metadata
        logger: Logger instance
        pr_number: PR number (optional, extracted from repo if not provided)
        pr_url: PR URL (optional)

    Returns:
        Metadata dictionary or None if failed
    """
    logger.info("Generating metadata")

    # Read patch files
    logger.debug("Reading patch files")

    # Full patch (for reference, not used in metadata)
    if not patch_file.exists():
        logger.error(f"Patch file not found: {patch_file}")
        return None

    with open(patch_file, 'r') as f:
        full_patch = f.read()

    # Generate code-only patch (excluding test files) for the "patch" field
    logger.debug("Generating code-only patch (excluding tests)")
    code_patch = generate_code_patch(repo_path, base_commit, pr_commit, logger)

    # Generate test-only patch for the "test_patch" field
    logger.debug("Generating test-only patch")
    test_patch = generate_test_patch(repo_path, base_commit, pr_commit, logger)

    logger.info(f"Generated patches: code={len(code_patch)} bytes, test={len(test_patch)} bytes, full={len(full_patch)} bytes")

    # Determine test output parser based on test command and language
    test_output_parser = determine_test_output_parser(test_command, language)
    logger.info(f"Selected test output parser: {test_output_parser}")

    # Get problem statement from GitHub
    if pr_number and pr_url:
        problem_statement = get_problem_statement(
            repo=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            logger=logger
        )
    else:
        # Fallback
        problem_statement = f"Repository {repo} at commit {pr_commit[:12]}"
        logger.warning("PR number/URL not provided - using fallback problem statement")

    # Convert test names to standard format and de-duplicate
    # This is important for parameterized tests which may have many variations
    # that all map to the same method name
    fail_to_pass_converted = convert_tests_to_relative_paths(fail_to_pass, repo, language)
    pass_to_pass_converted = convert_tests_to_relative_paths(pass_to_pass, repo, language)
    
    # De-duplicate and sort for consistent, clean output
    fail_to_pass_relative = sorted(set(fail_to_pass_converted))
    pass_to_pass_relative = sorted(set(pass_to_pass_converted))
    
    logger.info(f"Converted test paths to standard format (repo: {repo})")
    logger.info(f"  FAIL_TO_PASS: {len(fail_to_pass)} raw -> {len(fail_to_pass_relative)} unique")
    logger.info(f"  PASS_TO_PASS: {len(pass_to_pass)} raw -> {len(pass_to_pass_relative)} unique")
    if fail_to_pass and fail_to_pass_relative:
        logger.debug(f"  Example F2P: {fail_to_pass[0]} -> {fail_to_pass_relative[0]}")
    if pass_to_pass and pass_to_pass_relative:
        logger.debug(f"  Example P2P: {pass_to_pass[0]} -> {pass_to_pass_relative[0]}")

    # Convert image_uri to relative path from workspace root
    # image_uri format: file:///absolute/path/to/workspace/docker_images/image.tar
    # We want: docker_images/image.tar (relative to workspace)
    relative_image_uri = image_uri
    if image_uri.startswith("file://"):
        abs_image_path = image_uri[7:]  # Remove "file://" prefix
        # metadata_dir is {workspace}/metadata/, so parent is workspace
        workspace_root = metadata_dir.parent
        try:
            relative_image_path = Path(abs_image_path).relative_to(workspace_root)
            relative_image_uri = str(relative_image_path)
            logger.info(f"Converted image URI to relative path: {relative_image_uri}")
        except ValueError:
            # If not relative to workspace, keep as-is
            logger.warning(f"Could not make image URI relative, keeping absolute: {image_uri}")

    # Create metadata in the exact format specified:
    # {
    #   "instance_id": "<unique_identifier>",
    #   "repo": "<owner>/<repo_name>",
    #   "base_commit": "<git_commit_sha>",
    #   "problem_statement": "<description_of_the_problem_or_pr_message>",
    #   "hints_text": "<optional_hints_for_solving>",
    #   "FAIL_TO_PASS": "[\"test1\", \"test2\", ...]",
    #   "PASS_TO_PASS": "[\"test1\", \"test2\", ...]",
    #   "language": "<programming_language>",
    #   "test_command": "<command_to_run_tests>",
    #   "test_output_parser": "<parser_path>",
    #   "image_storage_uri": "<path_or_s3_uri_to_docker_image>",
    #   "patch": "<git_diff_of_the_solution>",
    #   "test_patch": "<git_diff_for_test_files>"
    # }
    #
    # Note: patch contains ONLY code (no test files)
    #       test_patch contains ONLY test files (no code)
    
    # Generate unique instance_id
    instance_id = generate_instance_id()
    
    metadata = {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": base_commit,
        "problem_statement": problem_statement,
        "hints_text": "",
        "FAIL_TO_PASS": json.dumps(fail_to_pass_relative),  # JSON string of test array
        "PASS_TO_PASS": json.dumps(pass_to_pass_relative),  # JSON string of test array
        "language": language,
        "test_command": test_command,
        "test_output_parser": test_output_parser,
        "image_storage_uri": relative_image_uri,
        "patch": code_patch,  # Code changes ONLY (no test files)
        "test_patch": test_patch  # Test file changes ONLY (no code)
    }

    # Save to file
    metadata_dir.mkdir(parents=True, exist_ok=True)
    
    # Save single instance format
    metadata_file = metadata_dir / "instance.json"
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"Metadata saved to {metadata_file}")
    
    # Also save in array format for harness compatibility (swe-bench-instance.json)
    # The entry script expects: jq '.[] | select(.instance_id == $ID)'
    harness_file = metadata_dir / "swe-bench-instance.json"
    with open(harness_file, 'w') as f:
        json.dump([metadata], f, indent=2)
    logger.info(f"Harness-compatible format saved to {harness_file}")

    # Log summary
    logger.info("Metadata summary:")
    logger.info(f"  instance_id: {metadata['instance_id']}")
    logger.info(f"  repo: {metadata['repo']}")
    logger.info(f"  base_commit: {metadata['base_commit']}")
    logger.info(f"  language: {metadata['language']}")
    logger.info(f"  test_command: {metadata['test_command']}")
    logger.info(f"  FAIL_TO_PASS count: {len(fail_to_pass)}")
    logger.info(f"  PASS_TO_PASS count: {len(pass_to_pass)}")
    logger.info(f"  image_uri: {metadata['image_storage_uri']}")
    logger.info(f"  patch size (code only): {len(code_patch)} bytes")
    logger.info(f"  test_patch size: {len(test_patch)} bytes")

    return metadata


def is_test_file(filepath: str) -> bool:
    """
    Determine if a file is a test file based on its path.
    
    This function uses consistent logic for classifying files as test files,
    ensuring no overlap or gaps between code and test patches.
    
    Args:
        filepath: File path relative to repository root
        
    Returns:
        True if the file is a test file, False otherwise
    """
    import re
    
    filepath_lower = filepath.lower()
    
    # Patterns that indicate a test file (checked in order of specificity)
    
    # 1. Directory-based patterns (most reliable)
    dir_patterns = [
        r'^tests?/',           # Starts with test/ or tests/
        r'/tests?/',           # Contains /test/ or /tests/
        r'^spec/',             # Starts with spec/
        r'/spec/',             # Contains /spec/
        r'testdata/',          # Test data directories
        r'test_data/',         # Test data directories
        r'testing/',           # Testing directories (but not 'testing' as part of a word)
        r'fixtures/',          # Test fixtures
        r'__tests__/',         # Jest-style test directories
        r'__mocks__/',         # Jest-style mock directories
        r'/mocks?/',           # Mock directories
    ]
    
    # 2. Filename-based patterns (language-specific)
    file_patterns = [
        r'_test\.[^/]+$',      # Go: *_test.go, Python: *_test.py
        r'_spec\.[^/]+$',      # Ruby/JS: *_spec.rb, *_spec.js
        r'\.test\.[^/]+$',     # JS/TS: *.test.js, *.test.ts
        r'\.spec\.[^/]+$',     # JS/TS: *.spec.js, *.spec.ts
        r'test_[^/]+\.[^/]+$', # Python: test_*.py
        r'Test[^/]*\.java$',   # Java: Test*.java, *Test.java (handled below)
        r'Tests?\.java$',      # Java: *Test.java, *Tests.java
        r'IT\.java$',          # Java integration tests: *IT.java
        r'[^/]*Test\.java$',   # Java: SomeClassTest.java
        r'[^/]*Tests\.java$',  # Java: SomeClassTests.java
        r'[^/]*Test\.kt$',     # Kotlin: *Test.kt
        r'[^/]*Test\.scala$',  # Scala: *Test.scala
        r'[^/]*Spec\.scala$',  # Scala: *Spec.scala
    ]
    
    # Check directory patterns first
    for pattern in dir_patterns:
        if re.search(pattern, filepath_lower):
            return True
    
    # Check filename patterns
    for pattern in file_patterns:
        if re.search(pattern, filepath, re.IGNORECASE):
            return True
    
    return False


def classify_changed_files(
    repo_path: 'Path',
    base_commit: str,
    pr_commit: str,
    logger: 'logging.Logger'
) -> tuple:
    """
    Classify all changed files into code files and test files.
    
    Uses consistent logic to ensure:
    - No overlap: a file cannot be in both categories
    - No gaps: every changed file is in exactly one category
    
    Args:
        repo_path: Path to repository
        base_commit: BASE commit SHA
        pr_commit: PR commit SHA
        logger: Logger instance
        
    Returns:
        Tuple of (code_files, test_files) - lists of file paths
    """
    from .git_operations import run_command
    
    # Get list of all changed files
    cmd_files = ["git", "diff", "--name-only", base_commit, pr_commit]
    exit_code, stdout, stderr = run_command(cmd_files, cwd=repo_path, logger=logger)
    
    if exit_code != 0:
        logger.warning(f"Failed to get changed files: {stderr}")
        return [], []
    
    all_files = [f.strip() for f in stdout.strip().split('\n') if f.strip()]
    
    if not all_files:
        logger.debug("No changed files found")
        return [], []
    
    # Classify files using consistent logic
    code_files = []
    test_files = []
    
    for filepath in all_files:
        if is_test_file(filepath):
            test_files.append(filepath)
        else:
            code_files.append(filepath)
    
    logger.info(f"Classified {len(all_files)} files: {len(code_files)} code, {len(test_files)} test")
    
    # Log some examples for debugging
    if test_files and logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"  Test file examples: {test_files[:3]}")
    if code_files and logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"  Code file examples: {code_files[:3]}")
    
    return code_files, test_files


def generate_test_patch(
    repo_path: 'Path',
    base_commit: str,
    pr_commit: str,
    logger: 'logging.Logger',
    language: Optional[str] = None
) -> str:
    """
    Generate patch containing only test code.
    
    Uses hunk-level analysis to separate test code from production code.
    This handles languages where tests are embedded in the same file as
    production code (e.g., Rust's #[cfg(test)], Python doctests).

    The process:
    1. Generate full diff between commits
    2. Parse diff into file diffs and individual hunks
    3. Classify each hunk as test/code based on content patterns
    4. Reconstruct patch with only test-related hunks

    Args:
        repo_path: Path to repository
        base_commit: BASE commit SHA
        pr_commit: PR commit SHA
        logger: Logger instance
        language: Optional language hint (auto-detected per file if None)

    Returns:
        Test-only patch as string (may include hunks from mixed files)
    """
    from .git_operations import run_command
    from .diff_parser import (
        parse_diff,
        classify_all_hunks,
        reconstruct_patch,
        get_patch_statistics,
        HunkType,
        INLINE_TEST_LANGUAGES
    )
    
    # Step 1: Generate full diff
    cmd = ["git", "diff", "--binary", "--full-index", base_commit, pr_commit]
    exit_code, full_diff, stderr = run_command(cmd, cwd=repo_path, logger=logger)
    
    if exit_code != 0:
        logger.warning(f"Failed to generate diff: {stderr}")
        return ""
    
    if not full_diff.strip():
        logger.debug("No changes between commits")
        return ""
    
    # Step 2: Parse diff into files and hunks
    file_diffs = parse_diff(full_diff)
    logger.debug(f"Parsed {len(file_diffs)} files from diff")
    
    # Step 3: Classify all hunks
    classify_all_hunks(file_diffs, language, logger)
    
    # Log statistics for debugging
    stats = get_patch_statistics(full_diff, language, logger)
    logger.info(f"Hunk classification: {stats['test_hunks']} test, {stats['code_hunks']} code, "
                f"{stats['mixed_hunks']} mixed, {stats['unknown_hunks']} unknown")
    if stats['mixed_files']:
        logger.info(f"Mixed files (containing both test and code): {stats['mixed_files']}")
    
    # Step 4: Reconstruct test-only patch
    # Include TEST and MIXED hunks (conservative: include borderline cases in test patch)
    test_patch = reconstruct_patch(
        file_diffs,
        include_types={HunkType.TEST, HunkType.MIXED},
        logger=logger
    )
    
    logger.info(f"Generated test patch: {len(test_patch)} bytes "
                f"({stats['test_hunks']} test + {stats['mixed_hunks']} mixed hunks)")
    
    return test_patch


def generate_code_patch(
    repo_path: 'Path',
    base_commit: str,
    pr_commit: str,
    logger: 'logging.Logger',
    language: Optional[str] = None
) -> str:
    """
    Generate patch containing only production code (excluding test code).
    
    Uses hunk-level analysis to separate production code from test code.
    This handles languages where tests are embedded in the same file as
    production code (e.g., Rust's #[cfg(test)], Python doctests).

    The process:
    1. Generate full diff between commits
    2. Parse diff into file diffs and individual hunks
    3. Classify each hunk as test/code based on content patterns
    4. Reconstruct patch with only code-related hunks

    Args:
        repo_path: Path to repository
        base_commit: BASE commit SHA
        pr_commit: PR commit SHA
        logger: Logger instance
        language: Optional language hint (auto-detected per file if None)

    Returns:
        Code-only patch as string (excluding test hunks)
    """
    from .git_operations import run_command
    from .diff_parser import (
        parse_diff,
        classify_all_hunks,
        reconstruct_patch,
        get_patch_statistics,
        HunkType
    )
    
    # Step 1: Generate full diff
    cmd = ["git", "diff", "--binary", "--full-index", base_commit, pr_commit]
    exit_code, full_diff, stderr = run_command(cmd, cwd=repo_path, logger=logger)
    
    if exit_code != 0:
        logger.warning(f"Failed to generate diff: {stderr}")
        return ""
    
    if not full_diff.strip():
        logger.debug("No changes between commits")
        return ""
    
    # Step 2: Parse diff into files and hunks
    file_diffs = parse_diff(full_diff)
    logger.debug(f"Parsed {len(file_diffs)} files from diff")
    
    # Step 3: Classify all hunks
    classify_all_hunks(file_diffs, language, logger)
    
    # Log statistics for debugging
    stats = get_patch_statistics(full_diff, language, logger)
    logger.debug(f"Hunk classification: {stats['test_hunks']} test, {stats['code_hunks']} code, "
                 f"{stats['mixed_hunks']} mixed, {stats['unknown_hunks']} unknown")
    
    # Step 4: Reconstruct code-only patch
    # Include CODE and UNKNOWN hunks (default unknown to code)
    code_patch = reconstruct_patch(
        file_diffs,
        include_types={HunkType.CODE, HunkType.UNKNOWN},
        logger=logger
    )
    
    logger.info(f"Generated code patch: {len(code_patch)} bytes "
                f"({stats['code_hunks']} code + {stats['unknown_hunks']} unknown hunks)")
    
    return code_patch


def validate_artifacts(artifacts_dir: Path, logger: logging.Logger) -> bool:
    """
    Validate test artifacts directory.

    Checks for required files:
    - result.json

    Optional files (warn only if missing):
    - results.jsonl (written by container_runner)
    - result_summary.json

    Args:
        artifacts_dir: Path to artifacts directory
        logger: Logger instance

    Returns:
        True if validation passed
    """
    if not artifacts_dir.exists():
        logger.error(f"Artifacts directory not found: {artifacts_dir}")
        return False

    required_files = ["result.json"]
    optional_files = ["results.jsonl", "result_summary.json"]

    errors = []

    # Check required files
    for filename in required_files:
        filepath = artifacts_dir / filename
        if not filepath.exists():
            errors.append(f"Missing required file: {filename}")

    # Check optional files (warn only)
    for filename in optional_files:
        filepath = artifacts_dir / filename
        if not filepath.exists():
            logger.warning(f"Optional file not found: {filename}")

    # Check for JUnit XML (optional)
    junit_files = list(artifacts_dir.glob("*junit*.xml"))
    if not junit_files:
        logger.warning("No JUnit XML report found")

    # Check for HTML reports (optional)
    html_files = list(artifacts_dir.glob("*.html"))
    if not html_files:
        logger.warning("No HTML report found")

    if errors:
        logger.error(f"Validation failed with {len(errors)} errors:")
        for error in errors:
            logger.error(f"  - {error}")
        return False

    logger.info("Artifacts validation passed")
    return True
