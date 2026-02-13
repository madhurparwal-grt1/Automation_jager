"""
Test result categorization for PR evaluation.

This module categorizes tests into:
- FAIL_TO_PASS: Tests that the PR fixes or adds
- PASS_TO_PASS: Tests relevant to the PR that should continue passing
"""

import json
import logging
import re
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Set


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


def normalize_test_set(tests: Set[str]) -> Set[str]:
    """
    Normalize a set of test names to handle timing variations.
    
    Args:
        tests: Set of raw test names
        
    Returns:
        Set of normalized test names
    """
    return {normalize_test_name(t) for t in tests}


def extract_changed_files_from_patch(patch_content: str) -> Set[str]:
    """
    Extract list of files changed in a patch.

    Args:
        patch_content: Content of the patch file (git diff output)

    Returns:
        Set of file paths that were modified
    """
    changed_files = set()

    # Pattern to match diff headers: "diff --git a/path/to/file b/path/to/file"
    diff_pattern = re.compile(r'^diff --git a/(.+?) b/(.+?)$', re.MULTILINE)

    for match in diff_pattern.finditer(patch_content):
        # Use the 'b' path (new file path)
        file_path = match.group(2)
        changed_files.add(file_path)

    return changed_files


def extract_changed_modules(changed_files: Set[str], language: str) -> Set[str]:
    """
    Extract module/package names from changed file paths.

    This helps match test names to changed code areas.
    Only extracts specific packages (not overly broad parent packages).

    Args:
        changed_files: Set of changed file paths
        language: Programming language

    Returns:
        Set of module/package identifiers
    """
    modules = set()

    for file_path in changed_files:
        path = Path(file_path)

        if language == "java":
            # Java: extract package path from src/main/java or src/test/java
            # e.g., "src/main/java/com/example/MyClass.java" -> "com.example.MyClass"
            parts = path.parts
            if "java" in parts:
                java_idx = parts.index("java")
                package_parts = parts[java_idx + 1:]
                if package_parts:
                    # Remove .java extension and join with dots
                    class_path = ".".join(package_parts)
                    if class_path.endswith(".java"):
                        class_path = class_path[:-5]

                    # Add the full class path
                    modules.add(class_path)

                    # Add the immediate package (class's parent package)
                    # e.g., "com.alibaba.nacos.core.plugin" from "com.alibaba.nacos.core.plugin.PluginManager"
                    if len(package_parts) > 1:
                        immediate_package = ".".join(package_parts[:-1])
                        if immediate_package.endswith(".java"):
                            immediate_package = immediate_package[:-5]
                        modules.add(immediate_package)

                    # Add just the class name for direct matching
                    class_name = package_parts[-1]
                    if class_name.endswith(".java"):
                        class_name = class_name[:-5]
                    modules.add(class_name)

                    # DON'T add all parent packages - they're too broad
                    # This was causing com, com.alibaba, etc. to match everything

        elif language == "python":
            # Python: extract module path
            # e.g., "src/mypackage/module.py" -> "mypackage.module"
            if path.suffix == ".py":
                # Remove common prefixes like src/, lib/, etc.
                parts = list(path.parts)
                for prefix in ["src", "lib", "tests", "test"]:
                    if prefix in parts:
                        parts = parts[parts.index(prefix) + 1:]
                        break
                # Join and remove .py
                module_path = ".".join(parts)
                if module_path.endswith(".py"):
                    module_path = module_path[:-3]
                modules.add(module_path)
                # Add parent modules
                for i in range(1, len(parts)):
                    modules.add(".".join(parts[:i]))

        elif language in ("javascript", "typescript"):
            # JS/TS: use file path components
            parts = path.parts
            # Skip common prefixes
            for prefix in ["src", "lib", "dist", "test", "tests", "__tests__"]:
                if prefix in parts:
                    idx = parts.index(prefix)
                    parts = parts[idx:]
                    break
            # Add each component
            for part in parts:
                if part.endswith((".js", ".ts", ".jsx", ".tsx")):
                    part = part.rsplit(".", 1)[0]
                modules.add(part)

        elif language == "go":
            # Go: extract package path
            # e.g., "internal/pkg/mypackage/file.go" -> "mypackage"
            if path.suffix == ".go":
                # Use directory name as package
                modules.add(path.parent.name)
                # Also add path components
                for part in path.parts[:-1]:
                    if part not in (".", "..", "internal", "pkg", "cmd"):
                        modules.add(part)

        elif language == "php":
            # PHP: extract namespace path from src/ or lib/ directory
            # e.g., "src/League/Csv/AbstractCsv.php" -> "League.Csv.AbstractCsv"
            if path.suffix == ".php":
                parts = list(path.parts)
                # Remove common prefixes like src/, lib/, tests/
                for prefix in ["src", "lib", "tests", "test"]:
                    if prefix in parts:
                        parts = parts[parts.index(prefix) + 1:]
                        break
                # Build namespace-like path
                namespace_path = ".".join(parts)
                if namespace_path.endswith(".php"):
                    namespace_path = namespace_path[:-4]
                modules.add(namespace_path)
                # Add just the class name
                class_name = path.stem
                modules.add(class_name)
                # Add parent package/namespace
                if len(parts) > 1:
                    modules.add(".".join(parts[:-1]))

        else:
            # Generic: use file stem and directory names
            modules.add(path.stem)
            for part in path.parts[:-1]:
                if part not in (".", ".."):
                    modules.add(part)

    return modules


def is_test_relevant_to_changes(
    test_name: str,
    changed_files: Set[str],
    changed_modules: Set[str],
    language: str
) -> bool:
    """
    Determine if a test is relevant to the changed files.

    A test is considered relevant ONLY if:
    1. The test file itself was changed (new test or modified test)
    2. The test class name directly relates to a changed class name
       (e.g., "PluginManagerTest" tests "PluginManager")

    This function is intentionally STRICT to avoid false positives.
    We do NOT match based on package alone - that's too broad.

    Args:
        test_name: Full test name (e.g., "com.example.TestClass#testMethod")
        changed_files: Set of changed file paths
        changed_modules: Set of changed module/package identifiers
        language: Programming language

    Returns:
        True if test is relevant to the changes
    """
    if language == "java":
        # For Java, extract the test's class name and method
        # Test name format: "com.example.TestClass#testMethod"
        test_class_full = test_name.split("#")[0] if "#" in test_name else test_name
        test_method = test_name.split("#")[1] if "#" in test_name else ""
        test_parts = test_class_full.split(".")
        test_class_name = test_parts[-1] if test_parts else ""
        test_package = ".".join(test_parts[:-1]) if len(test_parts) > 1 else ""

        # Remove common test suffixes to get the base class being tested
        base_class_being_tested = test_class_name
        is_integration_test = False
        for suffix in ["Test", "Tests", "IT", "IntegrationTest", "UnitTest"]:
            if base_class_being_tested.endswith(suffix):
                if suffix in ("IT", "IntegrationTest"):
                    is_integration_test = True
                base_class_being_tested = base_class_being_tested[:-len(suffix)]
                break

        # Check if any changed file corresponds to the class being tested
        for changed_file in changed_files:
            file_stem = Path(changed_file).stem  # e.g., "PluginManager" from "PluginManager.java"

            # Direct match: test class tests the changed class
            # e.g., "PluginManagerTest" tests "PluginManager"
            if file_stem == base_class_being_tested:
                return True

            # Also check if test class contains the changed class name
            # e.g., "PluginManagerIntegrationTest" tests "PluginManager"
            if len(file_stem) > 3 and file_stem in test_class_name:
                return True

        # Check if the test file itself was changed
        for changed_file in changed_files:
            file_stem = Path(changed_file).stem
            if file_stem == test_class_name:
                return True

        # Additional checks for integration tests and method-level matching
        # These are more lenient to catch tests that exercise changed functionality

        # Check 1: Test method name contains keywords from changed files
        # e.g., "kotlinFullFeatureTest" matches changes in kotlin/ directory
        if test_method:
            test_method_lower = test_method.lower()
            for changed_file in changed_files:
                # Extract meaningful keywords from the path
                path_parts = changed_file.lower().replace("\\", "/").split("/")
                for part in path_parts:
                    # Skip generic path components
                    if part in ("src", "main", "test", "java", "kotlin", "resources", ""):
                        continue
                    # Check if any path component (like "kotlin") appears in test method
                    if len(part) > 3 and part in test_method_lower:
                        return True

        # Check 2: Package path overlap
        # e.g., test in "org.mapstruct.ap.test.kotlin" matches file in "org/mapstruct/ap/.../kotlin/"
        if test_package:
            test_package_parts = set(test_package.lower().split("."))
            for changed_file in changed_files:
                # Convert file path to package-like format
                path_lower = changed_file.lower().replace("\\", "/")
                path_parts = set(path_lower.split("/"))
                # Check for significant package component overlap (excluding common ones)
                common_parts = {"src", "main", "test", "java", "kotlin", "resources", "org", "com"}
                meaningful_test_parts = test_package_parts - common_parts
                meaningful_path_parts = path_parts - common_parts
                overlap = meaningful_test_parts & meaningful_path_parts
                if len(overlap) >= 2:  # At least 2 meaningful components match
                    return True

        # Check 3: For Maven/integration tests, check if significant source files changed
        # Integration tests often exercise multiple modules
        if is_integration_test or "itest" in test_package.lower() or "integration" in test_class_name.lower():
            # Count changed source files (not test files)
            source_files_changed = sum(
                1 for f in changed_files
                if "/src/main/" in f.replace("\\", "/") or
                   (not "/test/" in f.replace("\\", "/") and f.endswith((".java", ".kt")))
            )
            # If substantial source changes, integration tests are likely relevant
            if source_files_changed >= 3:
                return True

        return False

    elif language == "python":
        # For Python, match test file to source file
        # e.g., "test_module.py" tests "module.py"
        test_name_lower = test_name.lower()

        for changed_file in changed_files:
            file_stem = Path(changed_file).stem.lower()
            if len(file_stem) > 3:
                # Check various test naming conventions
                if f"test_{file_stem}" in test_name_lower:
                    return True
                if f"{file_stem}_test" in test_name_lower:
                    return True
                if file_stem in test_name_lower:
                    return True

        return False

    elif language in ("javascript", "typescript"):
        # For JS/TS, match test file to source file
        # e.g., "module.test.js" tests "module.js"
        test_name_lower = test_name.lower()

        for changed_file in changed_files:
            file_stem = Path(changed_file).stem.lower()
            # Remove .test, .spec suffixes if present
            file_stem = file_stem.replace(".test", "").replace(".spec", "")
            if len(file_stem) > 3 and file_stem in test_name_lower:
                return True

        return False

    elif language == "php":
        # For PHP, match test class to source class
        # Test name format: "Namespace\ClassName::testMethodName" or "Namespace\ClassName::testMethodWithDataSet\"dataSetName\""
        # Example: League\Csv\AbstractCsv::testStreamFilterModeWithDataSet"readerWithStreamCapability"

        # Extract the class being tested from the test name
        test_class_full = test_name.split("::")[0] if "::" in test_name else test_name
        test_class_parts = test_class_full.split("\\")
        test_class_name = test_class_parts[-1] if test_class_parts else ""

        # Remove common test suffixes to get the base class being tested
        base_class_being_tested = test_class_name
        for suffix in ["Test", "Tests"]:
            if base_class_being_tested.endswith(suffix):
                base_class_being_tested = base_class_being_tested[:-len(suffix)]
                break

        # Check if any changed file corresponds to the class being tested
        for changed_file in changed_files:
            file_stem = Path(changed_file).stem  # e.g., "AbstractCsv" from "AbstractCsv.php"

            # Direct match: test class tests the changed class
            # e.g., "AbstractCsvTest" tests "AbstractCsv"
            if file_stem == base_class_being_tested:
                return True

            # Also check if test class contains the changed class name
            if len(file_stem) > 3 and file_stem in test_class_name:
                return True

            # Check if file name matches test class name exactly
            if file_stem == test_class_name:
                return True

        # Check namespace overlap (for integration tests)
        if test_class_full:
            test_namespace_parts = set(p.lower() for p in test_class_parts[:-1])
            for changed_file in changed_files:
                # Convert file path to namespace-like format
                path_lower = changed_file.lower().replace("\\", "/")
                path_parts = set(path_lower.replace("/", "\\").split("\\"))
                # Check for significant namespace component overlap
                common_parts = {"src", "lib", "tests", "test", "vendor"}
                meaningful_test_parts = test_namespace_parts - common_parts
                meaningful_path_parts = path_parts - common_parts
                overlap = meaningful_test_parts & meaningful_path_parts
                if len(overlap) >= 2:  # At least 2 meaningful components match
                    return True

        return False

    elif language == "ruby":
        # For Ruby Minitest, test names are in format "ClassName#method_name" or "ClassName::NestedClass#method_name"
        # e.g., "RenderTagTest#test_render_attribute_with_invalid_expression"
        # e.g., "SnippetTest::LaxMode#test_valid_inline_snippet"

        # Handle synthetic test names (test_1, test_2, failed_test_1, etc.) - these can't be matched meaningfully
        # They are generated when the minitest verbose parser can't extract real names
        # Also handle example_1, passed_test_1 patterns from RSpec fallback
        if re.match(r'^(test_|failed_test_|skipped_test_|example_|pending_example_|passed_test_)\d+$', test_name):
            # For synthetic names, be lenient - include them if there are any test file changes
            for changed_file in changed_files:
                # Quick inline check if file is a test file
                cf_lower = changed_file.lower()
                if '/test/' in cf_lower or '/tests/' in cf_lower or '/spec/' in cf_lower:
                    return True
                if '_test.rb' in cf_lower or '_spec.rb' in cf_lower or 'test_' in Path(changed_file).stem.lower():
                    return True
            return False

        # Extract the class name from the test name
        test_class_name = ""
        if "#" in test_name:
            test_class_name = test_name.split("#")[0]
        elif "::" in test_name:
            # Handle nested classes
            test_class_name = test_name.split("::")[0]

        if not test_class_name:
            # Can't extract class, use generic matching
            test_name_lower = test_name.lower()
            for changed_file in changed_files:
                file_stem = Path(changed_file).stem.lower()
                if len(file_stem) > 3 and file_stem in test_name_lower:
                    return True
            return False

        # Remove common test suffixes to get the base class being tested
        # e.g., "RenderTagTest" -> "RenderTag", "SnippetTest" -> "Snippet"
        base_class_being_tested = test_class_name
        for suffix in ["Test", "Tests", "Spec"]:
            if base_class_being_tested.endswith(suffix):
                base_class_being_tested = base_class_being_tested[:-len(suffix)]
                break

        # Convert CamelCase to snake_case for file matching
        # e.g., "RenderTag" -> "render_tag", "SnippetDrop" -> "snippet_drop"
        def camel_to_snake(name):
            s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

        base_class_snake = camel_to_snake(base_class_being_tested)
        test_class_snake = camel_to_snake(test_class_name)

        # Check if any changed file corresponds to the class being tested
        for changed_file in changed_files:
            file_stem = Path(changed_file).stem.lower()
            file_path_lower = changed_file.lower()

            # Direct match: test class tests the changed file
            # e.g., "RenderTagTest" tests "render_tag.rb" or "render.rb"
            if file_stem == base_class_snake:
                return True

            # Also match if the base class name is contained in the file stem
            # e.g., "SnippetDropTest" matches "snippet_drop.rb"
            if len(base_class_snake) > 3 and base_class_snake in file_stem:
                return True

            # Check if file name contains the class being tested (CamelCase)
            if len(base_class_being_tested) > 3 and base_class_being_tested.lower() in file_path_lower:
                return True

            # Match by directory/module name (e.g., "lib/liquid/tags/render.rb" matches "RenderTagTest")
            path_parts = changed_file.replace("\\", "/").split("/")
            for part in path_parts:
                part_stem = Path(part).stem.lower()
                if len(part_stem) > 3:
                    # Check if path component matches test class
                    if part_stem in test_class_snake or part_stem in base_class_snake:
                        return True

        return False

    else:
        # Generic matching for other languages
        test_name_lower = test_name.lower()
        for changed_file in changed_files:
            file_stem = Path(changed_file).stem.lower()
            if len(file_stem) > 3 and file_stem in test_name_lower:
                return True

        return False


def categorize_tests_three_run(
    run1_result: Optional[Dict[str, Any]],
    run2_result: Optional[Dict[str, Any]],
    run3_result: Optional[Dict[str, Any]],
    logger: logging.Logger,
    patch_content: Optional[str] = None,
    language: str = "python"
) -> Tuple[List[str], List[str]]:
    """
    Categorize tests using the THREE-RUN SEQUENCE (proper SWE-bench validation).

    The three-run sequence:
    - Run 1: BASE state, no patches applied. Full test suite baseline.
    - Run 2: TEST_PATCH only (new tests added by PR). New tests MUST FAIL.
    - Run 3: FULL PATCH (test_patch + solution). New tests must PASS. No regressions.

    FAIL_TO_PASS (F2P): Tests that FAIL in Run 2 AND PASS in Run 3.
        This proves the test actually tests the bug/feature being fixed.

    PASS_TO_PASS (P2P): Tests that PASS in Run 1 AND PASS in Run 3 (excluding F2P).
        These are regression tests that should continue passing.

    Args:
        run1_result: Run 1 results (BASE, no patches)
        run2_result: Run 2 results (test_patch only applied)
        run3_result: Run 3 results (full patch applied)
        logger: Logger instance
        patch_content: Content of the full patch file (for filtering P2P relevance)
        language: Programming language (for module extraction)

    Returns:
        Tuple of (fail_to_pass, pass_to_pass) test name lists
    """
    logger.info("=" * 60)
    logger.info("CATEGORIZING TESTS USING THREE-RUN SEQUENCE")
    logger.info("=" * 60)
    logger.info("F2P = FAIL in Run 2 (test_patch only) AND PASS in Run 3 (full patch)")
    logger.info("P2P = PASS in Run 1 (base) AND PASS in Run 3 (full patch), excluding F2P")
    logger.info("")

    fail_to_pass = []
    pass_to_pass = []

    # Handle missing results
    if not run1_result:
        logger.error("Missing Run 1 (BASE) results - cannot categorize")
        return fail_to_pass, pass_to_pass
    if not run3_result:
        logger.error("Missing Run 3 (FULL PATCH) results - cannot categorize")
        return fail_to_pass, pass_to_pass

    # Extract and normalize test sets from Run 1 (BASE)
    run1_passed = normalize_test_set(set(
        run1_result.get("tests_passed", run1_result.get("passed", []))
    ))
    run1_failed = normalize_test_set(set(
        run1_result.get("tests_failed", run1_result.get("failed", []))
    ))

    # Extract and normalize test sets from Run 3 (FULL PATCH)
    run3_passed = normalize_test_set(set(
        run3_result.get("tests_passed", run3_result.get("passed", []))
    ))
    run3_failed = normalize_test_set(set(
        run3_result.get("tests_failed", run3_result.get("failed", []))
    ))

    # Extract and normalize test sets from Run 2 (TEST_PATCH only)
    # Run 2 is optional - if no test_patch or empty, we handle it gracefully
    if run2_result:
        run2_passed = normalize_test_set(set(
            run2_result.get("tests_passed", run2_result.get("passed", []))
        ))
        run2_failed = normalize_test_set(set(
            run2_result.get("tests_failed", run2_result.get("failed", []))
        ))
        has_run2 = True
    else:
        run2_passed = set()
        run2_failed = set()
        has_run2 = False
        logger.warning("No Run 2 results - will use fallback categorization")

    logger.info(f"Run 1 (BASE):       {len(run1_passed)} passed, {len(run1_failed)} failed")
    if has_run2:
        logger.info(f"Run 2 (TEST_PATCH): {len(run2_passed)} passed, {len(run2_failed)} failed")
    else:
        logger.info(f"Run 2 (TEST_PATCH): N/A (no test_patch or empty)")
    logger.info(f"Run 3 (FULL PATCH): {len(run3_passed)} passed, {len(run3_failed)} failed")
    logger.info("")

    # =========================================================================
    # FAIL_TO_PASS: Tests that FAIL in Run 2 AND PASS in Run 3
    # =========================================================================
    if has_run2:
        # Proper three-run validation
        for test in run3_passed:
            if test in run2_failed:
                # Test failed with only test_patch, passes with full patch
                # This is a properly validated F2P test
                fail_to_pass.append(test)
        
        logger.info(f"FAIL_TO_PASS (validated): {len(fail_to_pass)} tests")
        logger.info(f"  These tests FAIL in Run 2 (test_patch) and PASS in Run 3 (full patch)")
        
        # Also report tests that passed in Run 2 (potential issues)
        new_tests_that_passed_run2 = run2_passed - run1_passed - run1_failed
        if new_tests_that_passed_run2:
            logger.warning(f"  ⚠️  {len(new_tests_that_passed_run2)} new tests PASSED in Run 2 (may not test the fix!)")
            for test in sorted(list(new_tests_that_passed_run2))[:5]:
                logger.warning(f"      - {test[:80]}...")
    else:
        # Fallback: No Run 2 results - use legacy two-run logic
        # F2P = tests that failed in Run 1 OR are new, and pass in Run 3
        logger.warning("Using fallback categorization (no Run 2 validation)")
        for test in run3_passed:
            if test in run1_failed and test not in run1_passed:
                # Failed in BASE, passes in FULL PATCH
                fail_to_pass.append(test)
            elif test not in run1_passed and test not in run1_failed:
                # New test added by PR (not in Run 1 at all)
                fail_to_pass.append(test)
        
        logger.info(f"FAIL_TO_PASS (fallback): {len(fail_to_pass)} tests")
        logger.info(f"  - Fixed tests (failed in Run 1, pass in Run 3): "
                   f"{len([t for t in fail_to_pass if t in run1_failed])}")
        logger.info(f"  - New tests (not in Run 1): "
                   f"{len([t for t in fail_to_pass if t not in run1_failed])}")

    # =========================================================================
    # PASS_TO_PASS: Tests that PASS in Run 1 AND PASS in Run 3 (excluding F2P)
    # =========================================================================
    f2p_set = set(fail_to_pass)
    
    # All tests that passed in both Run 1 and Run 3
    all_p2p_candidates = run1_passed & run3_passed
    
    # Exclude any F2P tests (though they shouldn't overlap by definition)
    all_p2p_candidates = all_p2p_candidates - f2p_set

    if patch_content:
        # Extract changed files and modules from patch for relevance filtering
        changed_files = extract_changed_files_from_patch(patch_content)
        changed_modules = extract_changed_modules(changed_files, language)

        logger.info(f"Patch analysis for P2P filtering:")
        logger.info(f"  - Changed files: {len(changed_files)}")
        logger.info(f"  - Changed modules: {len(changed_modules)}")

        # Filter PASS_TO_PASS to only include tests relevant to the PR changes
        for test in all_p2p_candidates:
            is_relevant = is_test_relevant_to_changes(test, changed_files, changed_modules, language)
            if is_relevant:
                pass_to_pass.append(test)

        logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} of {len(all_p2p_candidates)} tests are PR-relevant")
    else:
        # No patch provided - include all P2P candidates
        logger.warning("No patch content provided - including all P2P candidates")
        pass_to_pass = list(all_p2p_candidates)
        logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests")

    # =========================================================================
    # Summary
    # =========================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("CATEGORIZATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"FAIL_TO_PASS: {len(fail_to_pass)} tests")
    logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests")
    
    # Check for regressions (tests that passed in Run 1 but fail in Run 3)
    regressions = run1_passed & run3_failed
    if regressions:
        logger.warning(f"⚠️  REGRESSIONS DETECTED: {len(regressions)} tests passed in Run 1 but fail in Run 3!")
        for test in sorted(list(regressions))[:5]:
            logger.warning(f"    - {test[:80]}...")
    else:
        logger.info("✓ No regressions detected")
    
    logger.info("=" * 60)

    return fail_to_pass, pass_to_pass


def categorize_tests(
    base_result: Optional[Dict[str, Any]],
    pr_result: Optional[Dict[str, Any]],
    logger: logging.Logger,
    patch_content: Optional[str] = None,
    language: str = "python",
    test_patch_only_result: Optional[Dict[str, Any]] = None
) -> Tuple[List[str], List[str]]:
    """
    Categorize tests into FAIL_TO_PASS and PASS_TO_PASS.

    If test_patch_only_result is provided, uses the three-run sequence:
    - Run 1: base_result (BASE, no patches)
    - Run 2: test_patch_only_result (test_patch only)
    - Run 3: pr_result (full patch)

    Otherwise, falls back to the legacy two-run categorization.

    Args:
        base_result: BASE test results dictionary with tests_passed/tests_failed lists
        pr_result: PR test results dictionary with tests_passed/tests_failed lists
        logger: Logger instance
        patch_content: Content of the patch file (optional, for filtering PASS_TO_PASS)
        language: Programming language (for module extraction)
        test_patch_only_result: Optional Run 2 results (test_patch only)

    Returns:
        Tuple of (fail_to_pass, pass_to_pass) test name lists
    """
    # If we have Run 2 results, use the proper three-run categorization
    if test_patch_only_result is not None:
        return categorize_tests_three_run(
            run1_result=base_result,
            run2_result=test_patch_only_result,
            run3_result=pr_result,
            logger=logger,
            patch_content=patch_content,
            language=language
        )

    # Legacy two-run categorization (backward compatibility)
    logger.info("Categorizing tests into FAIL_TO_PASS and PASS_TO_PASS (legacy two-run)")

    fail_to_pass = []
    pass_to_pass = []

    # Handle missing results
    if not base_result or not pr_result:
        logger.warning("Missing test results - cannot categorize")
        return fail_to_pass, pass_to_pass

    # Extract test lists from results
    # Handle both formats: {"passed": [...]} and {"tests_passed": [...]}
    # IMPORTANT: Normalize test names to handle timing suffixes that can vary between runs
    # (e.g., "test name 0.01s" vs "test name 0.02s" should be treated as the same test)
    base_passed_raw = set(base_result.get("tests_passed", base_result.get("passed", [])))
    base_failed_raw = set(base_result.get("tests_failed", base_result.get("failed", [])))
    pr_passed_raw = set(pr_result.get("tests_passed", pr_result.get("passed", [])))
    pr_failed_raw = set(pr_result.get("tests_failed", pr_result.get("failed", [])))
    
    # Normalize all test names to strip timing suffixes
    base_passed = normalize_test_set(base_passed_raw)
    base_failed = normalize_test_set(base_failed_raw)
    pr_passed = normalize_test_set(pr_passed_raw)
    pr_failed = normalize_test_set(pr_failed_raw)

    # FAIL_TO_PASS: Tests that failed in BASE but pass in PR
    # This includes:
    # 1. Tests that were failing and now pass (bug fixes)
    # 2. Tests that are new in the PR (only in pr_passed, not in base at all)
    #
    # IMPORTANT: Tests with mixed outcomes (in both base_passed AND base_failed due to
    # multi-module builds) should NOT be counted as FAIL_TO_PASS unless they ONLY failed
    # in BASE. This handles cases where Maven runs the same test multiple times across
    # different modules with different configurations.

    for test in pr_passed:
        if test in base_failed and test not in base_passed:
            # Failed in BASE (and never passed), now passing - definite fix
            fail_to_pass.append(test)
        elif test not in base_passed and test not in base_failed:
            # New test added by the PR
            fail_to_pass.append(test)

    # PASS_TO_PASS: Tests that passed in both BASE and PR
    # But ONLY include tests relevant to the PR changes
    all_passing_both = base_passed & pr_passed

    if patch_content:
        # Extract changed files and modules from patch
        changed_files = extract_changed_files_from_patch(patch_content)
        changed_modules = extract_changed_modules(changed_files, language)

        logger.info(f"Patch analysis:")
        logger.info(f"  - Changed files: {len(changed_files)}")
        logger.info(f"  - Changed modules: {len(changed_modules)}")
        logger.debug(f"  - Files: {sorted(changed_files)[:10]}...")
        logger.debug(f"  - Modules: {sorted(changed_modules)[:10]}...")

        # Filter PASS_TO_PASS to only include relevant tests
        match_count = 0
        for test in all_passing_both:
            is_relevant = is_test_relevant_to_changes(test, changed_files, changed_modules, language)
            if is_relevant:
                pass_to_pass.append(test)
                match_count += 1

        logger.info(f"Filtered PASS_TO_PASS: {len(pass_to_pass)} of {len(all_passing_both)} tests are PR-relevant")
    else:
        # No patch provided - fall back to all passing tests
        # This maintains backward compatibility but is not ideal
        logger.warning("No patch content provided - including all passing tests in PASS_TO_PASS")
        pass_to_pass = list(all_passing_both)

    logger.info(f"FAIL_TO_PASS: {len(fail_to_pass)} tests total")
    logger.info(f"  - Fixed tests (failed->passed): {len([t for t in fail_to_pass if t in base_failed])}")
    logger.info(f"  - New tests (added by PR):      {len([t for t in fail_to_pass if t not in base_failed])}")
    logger.info(f"PASS_TO_PASS: {len(pass_to_pass)} tests (PR-relevant only)")

    return fail_to_pass, pass_to_pass
