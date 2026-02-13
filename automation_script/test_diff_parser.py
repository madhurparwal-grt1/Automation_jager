#!/usr/bin/env python3
"""
Tests for the diff_parser module.

Run with: python -m pytest automation_script/test_diff_parser.py -v
Or directly: python automation_script/test_diff_parser.py
"""

import logging
from diff_parser import (
    parse_diff,
    classify_hunk,
    classify_file_hunks,
    classify_all_hunks,
    reconstruct_patch,
    generate_test_patch_from_hunks,
    generate_code_patch_from_hunks,
    get_patch_statistics,
    DiffHunk,
    FileDiff,
    HunkType,
    detect_language_from_filepath,
)

# Set up logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# =============================================================================
# Sample Diff Data for Testing
# =============================================================================

# Sample Rust diff with inline tests
RUST_DIFF_WITH_INLINE_TESTS = """diff --git a/src/lib.rs b/src/lib.rs
index abc1234..def5678 100644
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -10,6 +10,15 @@ pub fn add(a: i32, b: i32) -> i32 {
     a + b
 }
 
+pub fn multiply(a: i32, b: i32) -> i32 {
+    a * b
+}
+
+pub fn subtract(a: i32, b: i32) -> i32 {
+    a - b
+}
+
 #[cfg(test)]
 mod tests {
     use super::*;
@@ -20,4 +29,14 @@ mod tests {
     fn test_add() {
         assert_eq!(add(2, 3), 5);
     }
+
+    #[test]
+    fn test_multiply() {
+        assert_eq!(multiply(2, 3), 6);
+    }
+
+    #[test]
+    fn test_subtract() {
+        assert_eq!(subtract(5, 3), 2);
+    }
 }
"""

# Sample Python diff with doctests
PYTHON_DIFF_WITH_DOCTESTS = """diff --git a/mymodule.py b/mymodule.py
index abc1234..def5678 100644
--- a/mymodule.py
+++ b/mymodule.py
@@ -5,6 +5,18 @@ def calculate_sum(a, b):
     \"\"\"
     Calculate the sum of two numbers.
     
+    Examples:
+        >>> calculate_sum(2, 3)
+        5
+        >>> calculate_sum(-1, 1)
+        0
+    \"\"\"
+    return a + b
+
+def calculate_product(a, b):
+    \"\"\"
+    Calculate the product of two numbers.
+    
     Examples:
         >>> calculate_sum(2, 3)
         5
@@ -15,3 +27,10 @@ def calculate_sum(a, b):
 def helper_function():
     # Some helper code
     pass
+
+def new_helper():
+    '''
+    A new helper function.
+    '''
+    return True
"""

# Sample Java diff with separate test file
JAVA_DIFF_SEPARATE_FILES = """diff --git a/src/main/java/com/example/Calculator.java b/src/main/java/com/example/Calculator.java
index abc1234..def5678 100644
--- a/src/main/java/com/example/Calculator.java
+++ b/src/main/java/com/example/Calculator.java
@@ -5,4 +5,8 @@ public class Calculator {
     public int add(int a, int b) {
         return a + b;
     }
+
+    public int multiply(int a, int b) {
+        return a * b;
+    }
 }
diff --git a/src/test/java/com/example/CalculatorTest.java b/src/test/java/com/example/CalculatorTest.java
index 111111..222222 100644
--- a/src/test/java/com/example/CalculatorTest.java
+++ b/src/test/java/com/example/CalculatorTest.java
@@ -10,4 +10,10 @@ public class CalculatorTest {
     public void testAdd() {
         assertEquals(5, calculator.add(2, 3));
     }
+
+    @Test
+    public void testMultiply() {
+        assertEquals(6, calculator.multiply(2, 3));
+    }
 }
"""

# Sample Go diff (Go uses _test.go files, but Example functions are special)
GO_DIFF = """diff --git a/calculator.go b/calculator.go
index abc1234..def5678 100644
--- a/calculator.go
+++ b/calculator.go
@@ -5,3 +5,7 @@ package calculator
 func Add(a, b int) int {
     return a + b
 }
+
+func Multiply(a, b int) int {
+    return a * b
+}
diff --git a/calculator_test.go b/calculator_test.go
index 111111..222222 100644
--- a/calculator_test.go
+++ b/calculator_test.go
@@ -10,3 +10,9 @@ func TestAdd(t *testing.T) {
         t.Errorf("Expected 5, got %d", result)
     }
 }
+
+func TestMultiply(t *testing.T) {
+    if result := Multiply(2, 3); result != 6 {
+        t.Errorf("Expected 6, got %d", result)
+    }
+}
"""


# =============================================================================
# Test Functions
# =============================================================================

def test_parse_diff_basic():
    """Test basic diff parsing."""
    file_diffs = parse_diff(RUST_DIFF_WITH_INLINE_TESTS)
    
    assert len(file_diffs) == 1, f"Expected 1 file, got {len(file_diffs)}"
    
    file_diff = file_diffs[0]
    assert file_diff.filepath == "src/lib.rs"
    assert len(file_diff.hunks) == 2, f"Expected 2 hunks, got {len(file_diff.hunks)}"
    
    # First hunk should be code (new functions)
    # Second hunk should be test (test module)
    
    print(f"✓ Parsed {len(file_diffs)} files with {len(file_diff.hunks)} hunks")
    return True


def test_parse_diff_multiple_files():
    """Test parsing diff with multiple files."""
    file_diffs = parse_diff(JAVA_DIFF_SEPARATE_FILES)
    
    assert len(file_diffs) == 2, f"Expected 2 files, got {len(file_diffs)}"
    
    filepaths = [f.filepath for f in file_diffs]
    assert "src/main/java/com/example/Calculator.java" in filepaths
    assert "src/test/java/com/example/CalculatorTest.java" in filepaths
    
    print(f"✓ Parsed {len(file_diffs)} files correctly")
    return True


def test_classify_rust_hunks():
    """Test classification of Rust hunks with inline tests."""
    file_diffs = parse_diff(RUST_DIFF_WITH_INLINE_TESTS)
    classify_all_hunks(file_diffs, 'rust', logger)
    
    file_diff = file_diffs[0]
    
    # Check that we have both code and test hunks
    code_hunks = [h for h in file_diff.hunks if h.hunk_type == HunkType.CODE]
    test_hunks = [h for h in file_diff.hunks if h.hunk_type == HunkType.TEST]
    
    print(f"  Code hunks: {len(code_hunks)}, Test hunks: {len(test_hunks)}")
    
    # The first hunk (adding functions) should be CODE
    # The second hunk (test module changes) should be TEST
    assert len(code_hunks) >= 1, "Expected at least 1 code hunk"
    assert len(test_hunks) >= 1, "Expected at least 1 test hunk"
    
    print(f"✓ Rust hunks classified correctly: {len(code_hunks)} code, {len(test_hunks)} test")
    return True


def test_classify_python_doctests():
    """Test classification of Python hunks with doctests."""
    file_diffs = parse_diff(PYTHON_DIFF_WITH_DOCTESTS)
    classify_all_hunks(file_diffs, 'python', logger)
    
    file_diff = file_diffs[0]
    
    # Check hunk classifications
    for i, hunk in enumerate(file_diff.hunks):
        print(f"  Hunk {i}: type={hunk.hunk_type.value}, confidence={hunk.confidence:.2f}")
        print(f"    Context: {hunk.context[:50]}..." if hunk.context else "    (no context)")
    
    # At least some hunks should be classified as test (doctests)
    test_hunks = [h for h in file_diff.hunks if h.hunk_type in (HunkType.TEST, HunkType.MIXED)]
    
    print(f"✓ Python hunks analyzed: {len(test_hunks)} test/mixed hunks found")
    return True


def test_reconstruct_patch():
    """Test patch reconstruction from classified hunks."""
    file_diffs = parse_diff(RUST_DIFF_WITH_INLINE_TESTS)
    classify_all_hunks(file_diffs, 'rust', logger)
    
    # Reconstruct test-only patch
    test_patch = reconstruct_patch(
        file_diffs, 
        include_types={HunkType.TEST, HunkType.MIXED},
        logger=logger
    )
    
    # Reconstruct code-only patch
    code_patch = reconstruct_patch(
        file_diffs,
        include_types={HunkType.CODE, HunkType.UNKNOWN},
        logger=logger
    )
    
    print(f"  Test patch: {len(test_patch)} bytes")
    print(f"  Code patch: {len(code_patch)} bytes")
    
    # Both patches should be non-empty for this test case
    assert len(test_patch) > 0, "Test patch should not be empty"
    assert len(code_patch) > 0, "Code patch should not be empty"
    
    # Patches should be valid git diff format
    assert "diff --git" in test_patch or len(test_patch) == 0
    assert "diff --git" in code_patch or len(code_patch) == 0
    
    print(f"✓ Patches reconstructed successfully")
    return True


def test_generate_test_patch_from_hunks():
    """Test the high-level test patch generation function."""
    test_patch = generate_test_patch_from_hunks(RUST_DIFF_WITH_INLINE_TESTS, 'rust', logger)
    
    print(f"  Generated test patch: {len(test_patch)} bytes")
    
    # Should contain test-related content
    if test_patch:
        assert "#[test]" in test_patch or "#[cfg(test)]" in test_patch or "mod tests" in test_patch
    
    print(f"✓ Test patch generation works")
    return True


def test_generate_code_patch_from_hunks():
    """Test the high-level code patch generation function."""
    code_patch = generate_code_patch_from_hunks(RUST_DIFF_WITH_INLINE_TESTS, 'rust', logger)
    
    print(f"  Generated code patch: {len(code_patch)} bytes")
    
    # Should contain production code
    if code_patch:
        assert "pub fn" in code_patch or "fn " in code_patch
    
    print(f"✓ Code patch generation works")
    return True


def test_get_patch_statistics():
    """Test patch statistics gathering."""
    stats = get_patch_statistics(RUST_DIFF_WITH_INLINE_TESTS, 'rust', logger)
    
    print(f"  Total files: {stats['total_files']}")
    print(f"  Total hunks: {stats['total_hunks']}")
    print(f"  Test hunks: {stats['test_hunks']}")
    print(f"  Code hunks: {stats['code_hunks']}")
    print(f"  Mixed files: {stats['mixed_files']}")
    
    assert stats['total_files'] == 1
    assert stats['total_hunks'] == 2
    
    print(f"✓ Statistics calculated correctly")
    return True


def test_java_separate_files():
    """Test handling of Java project with separate test files."""
    file_diffs = parse_diff(JAVA_DIFF_SEPARATE_FILES)
    classify_all_hunks(file_diffs, 'java', logger)
    
    stats = get_patch_statistics(JAVA_DIFF_SEPARATE_FILES, 'java', logger)
    
    print(f"  Test-only files: {stats['test_only_files']}")
    print(f"  Code-only files: {stats['code_only_files']}")
    
    # The test file should be in test_only_files (based on path)
    # But our hunk-level analysis also looks at content
    
    test_patch = generate_test_patch_from_hunks(JAVA_DIFF_SEPARATE_FILES, 'java', logger)
    code_patch = generate_code_patch_from_hunks(JAVA_DIFF_SEPARATE_FILES, 'java', logger)
    
    print(f"  Test patch: {len(test_patch)} bytes")
    print(f"  Code patch: {len(code_patch)} bytes")
    
    # Test patch should contain @Test
    assert "@Test" in test_patch
    
    # Code patch should contain the Calculator class changes
    assert "multiply" in code_patch.lower()
    
    print(f"✓ Java separate files handled correctly")
    return True


def test_language_detection():
    """Test language detection from file paths."""
    test_cases = [
        ("src/lib.rs", "rust"),
        ("main.py", "python"),
        ("Calculator.java", "java"),
        ("app.ts", "typescript"),
        ("test.go", "go"),
        ("spec.rb", "ruby"),
        ("module.ex", "elixir"),
    ]
    
    for filepath, expected in test_cases:
        detected = detect_language_from_filepath(filepath)
        assert detected == expected, f"Expected {expected} for {filepath}, got {detected}"
    
    print(f"✓ Language detection works for all test cases")
    return True


def test_empty_diff():
    """Test handling of empty diff."""
    file_diffs = parse_diff("")
    assert len(file_diffs) == 0
    
    file_diffs = parse_diff("   \n  \n  ")
    assert len(file_diffs) == 0
    
    test_patch = generate_test_patch_from_hunks("", 'python', logger)
    assert test_patch == ""
    
    print(f"✓ Empty diff handled correctly")
    return True


def run_all_tests():
    """Run all test functions."""
    tests = [
        ("Parse diff basic", test_parse_diff_basic),
        ("Parse diff multiple files", test_parse_diff_multiple_files),
        ("Classify Rust hunks", test_classify_rust_hunks),
        ("Classify Python doctests", test_classify_python_doctests),
        ("Reconstruct patch", test_reconstruct_patch),
        ("Generate test patch from hunks", test_generate_test_patch_from_hunks),
        ("Generate code patch from hunks", test_generate_code_patch_from_hunks),
        ("Get patch statistics", test_get_patch_statistics),
        ("Java separate files", test_java_separate_files),
        ("Language detection", test_language_detection),
        ("Empty diff", test_empty_diff),
    ]
    
    passed = 0
    failed = 0
    
    print("=" * 60)
    print("Running diff_parser tests")
    print("=" * 60)
    
    for name, test_func in tests:
        print(f"\n[TEST] {name}")
        try:
            if test_func():
                passed += 1
        except AssertionError as e:
            print(f"✗ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ ERROR: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
