"""
Diff Parser and Hunk-Level Analysis

This module provides functionality to parse git diffs at the hunk level
and classify each hunk as test code or production code.

This is essential for languages where tests are embedded within the same
file as production code (e.g., Rust's #[cfg(test)], Python doctests).

Key Components:
- DiffHunk: Data class representing a single diff hunk
- FileDiff: Data class representing all hunks for a single file
- parse_diff(): Parse git diff output into structured hunks
- classify_hunks(): Classify hunks as test/code based on language
- reconstruct_patch(): Rebuild a valid git patch from selected hunks
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple
from enum import Enum


class HunkType(Enum):
    """Classification of a diff hunk."""
    CODE = "code"           # Production code
    TEST = "test"           # Test code
    MIXED = "mixed"         # Contains both test and code
    UNKNOWN = "unknown"     # Cannot determine


@dataclass
class DiffHunk:
    """
    Represents a single hunk from a git diff.
    
    A hunk is a contiguous block of changes in a file, marked by
    @@ -start,count +start,count @@ in git diff output.
    """
    # Hunk header line (e.g., "@@ -10,5 +10,7 @@ function_name")
    header: str
    
    # Line numbers
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    
    # Optional context from header (function/class name)
    context: str
    
    # The actual content lines (including +, -, and space prefixes)
    lines: List[str]
    
    # Classification
    hunk_type: HunkType = HunkType.UNKNOWN
    
    # Confidence score for classification (0.0 to 1.0)
    confidence: float = 0.0
    
    def get_added_lines(self) -> List[str]:
        """Get lines that were added (prefixed with +)."""
        return [line[1:] for line in self.lines if line.startswith('+')]
    
    def get_removed_lines(self) -> List[str]:
        """Get lines that were removed (prefixed with -)."""
        return [line[1:] for line in self.lines if line.startswith('-')]
    
    def get_context_lines(self) -> List[str]:
        """Get context lines (prefixed with space)."""
        return [line[1:] for line in self.lines if line.startswith(' ')]
    
    def get_all_content(self) -> str:
        """Get all content as a single string (without prefixes)."""
        content = []
        for line in self.lines:
            if line and line[0] in ('+', '-', ' '):
                content.append(line[1:])
            else:
                content.append(line)
        return '\n'.join(content)
    
    def to_patch_string(self) -> str:
        """Convert hunk back to patch format."""
        return self.header + '\n' + '\n'.join(self.lines)


@dataclass
class FileDiff:
    """
    Represents the complete diff for a single file.
    
    Contains the file headers and all hunks for that file.
    """
    # Original file path (from "--- a/path")
    old_path: str
    
    # New file path (from "+++ b/path")  
    new_path: str
    
    # The diff header lines (diff --git, index, ---, +++)
    header_lines: List[str]
    
    # All hunks in this file
    hunks: List[DiffHunk] = field(default_factory=list)
    
    # Extended header lines (for binary, mode changes, etc.)
    extended_header: List[str] = field(default_factory=list)
    
    # Is this a binary file?
    is_binary: bool = False
    
    # Is this a new file?
    is_new_file: bool = False
    
    # Is this a deleted file?
    is_deleted: bool = False
    
    @property
    def filepath(self) -> str:
        """Get the canonical file path (prefer new_path)."""
        if self.new_path and self.new_path != '/dev/null':
            return self.new_path
        return self.old_path
    
    def get_hunks_by_type(self, hunk_type: HunkType) -> List[DiffHunk]:
        """Get all hunks of a specific type."""
        return [h for h in self.hunks if h.hunk_type == hunk_type]
    
    def has_test_hunks(self) -> bool:
        """Check if this file has any test hunks."""
        return any(h.hunk_type == HunkType.TEST for h in self.hunks)
    
    def has_code_hunks(self) -> bool:
        """Check if this file has any code hunks."""
        return any(h.hunk_type == HunkType.CODE for h in self.hunks)
    
    def is_mixed_file(self) -> bool:
        """Check if this file has both test and code hunks."""
        return self.has_test_hunks() and self.has_code_hunks()

    def ordered_header_lines(self) -> List[str]:
        """
        Return diff header lines in git-compatible order.

        Git expects:
        1) `diff --git ...`
        2) extended header lines (mode/index/rename metadata)
        3) `---` / `+++` file markers
        """
        if not self.header_lines:
            return self.extended_header.copy()

        first = self.header_lines[:1]
        rest = self.header_lines[1:]
        return first + self.extended_header + rest
    
    def to_patch_string(self, include_types: Optional[Set[HunkType]] = None) -> str:
        """
        Convert file diff back to patch format.
        
        Args:
            include_types: If provided, only include hunks of these types.
                          If None, include all hunks.
        
        Returns:
            Valid git patch string for this file.
        """
        if include_types is not None:
            hunks_to_include = [h for h in self.hunks if h.hunk_type in include_types]
        else:
            hunks_to_include = self.hunks
        
        if not hunks_to_include:
            return ""
        
        # For binary files, we can't split hunks
        if self.is_binary:
            if include_types is None:
                return '\n'.join(self.ordered_header_lines())
            # For binary files, we need to decide: include all or nothing
            # Check if any hunk type matches (binary files have no real hunks)
            return '\n'.join(self.ordered_header_lines())
        
        lines = self.ordered_header_lines()
        
        for hunk in hunks_to_include:
            lines.append(hunk.header)
            lines.extend(hunk.lines)
        
        return '\n'.join(lines)


def parse_diff(diff_content: str) -> List[FileDiff]:
    """
    Parse git diff output into structured FileDiff objects.
    
    Handles:
    - Standard text diffs
    - Binary diffs
    - New/deleted files
    - Renamed files
    - Mode changes
    
    Args:
        diff_content: Raw git diff output string
        
    Returns:
        List of FileDiff objects, one per file in the diff
    """
    if not diff_content or not diff_content.strip():
        return []
    
    files: List[FileDiff] = []
    current_file: Optional[FileDiff] = None
    current_hunk: Optional[DiffHunk] = None
    
    # Regex patterns
    diff_header_pattern = re.compile(r'^diff --git a/(.+?) b/(.+?)$')
    old_file_pattern = re.compile(r'^--- (?:a/)?(.+)$')
    new_file_pattern = re.compile(r'^\+\+\+ (?:b/)?(.+)$')
    hunk_header_pattern = re.compile(
        r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$'
    )
    binary_pattern = re.compile(r'^Binary files .+ differ$')
    
    lines = diff_content.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Start of a new file diff
        diff_match = diff_header_pattern.match(line)
        if diff_match:
            # Save previous file if exists
            if current_file is not None:
                if current_hunk is not None:
                    current_file.hunks.append(current_hunk)
                    current_hunk = None
                files.append(current_file)
            
            # Start new file
            current_file = FileDiff(
                old_path=diff_match.group(1),
                new_path=diff_match.group(2),
                header_lines=[line]
            )
            i += 1
            continue
        
        # Extended header lines (index, mode, etc.)
        if current_file is not None and current_hunk is None:
            if line.startswith('index ') or line.startswith('old mode ') or \
               line.startswith('new mode ') or line.startswith('new file mode ') or \
               line.startswith('deleted file mode ') or line.startswith('similarity index ') or \
               line.startswith('rename from ') or line.startswith('rename to ') or \
               line.startswith('copy from ') or line.startswith('copy to '):
                current_file.extended_header.append(line)
                
                if 'new file mode' in line:
                    current_file.is_new_file = True
                elif 'deleted file mode' in line:
                    current_file.is_deleted = True
                    
                i += 1
                continue
        
        # Binary file marker
        if current_file is not None and binary_pattern.match(line):
            current_file.is_binary = True
            current_file.extended_header.append(line)
            i += 1
            continue
        
        # Old file path (---)
        old_match = old_file_pattern.match(line)
        if old_match and current_file is not None:
            current_file.header_lines.append(line)
            if old_match.group(1) != '/dev/null':
                current_file.old_path = old_match.group(1)
            i += 1
            continue
        
        # New file path (+++)
        new_match = new_file_pattern.match(line)
        if new_match and current_file is not None:
            current_file.header_lines.append(line)
            if new_match.group(1) != '/dev/null':
                current_file.new_path = new_match.group(1)
            i += 1
            continue
        
        # Hunk header
        hunk_match = hunk_header_pattern.match(line)
        if hunk_match and current_file is not None:
            # Save previous hunk if exists
            if current_hunk is not None:
                current_file.hunks.append(current_hunk)
            
            # Start new hunk
            current_hunk = DiffHunk(
                header=line,
                old_start=int(hunk_match.group(1)),
                old_count=int(hunk_match.group(2) or 1),
                new_start=int(hunk_match.group(3)),
                new_count=int(hunk_match.group(4) or 1),
                context=hunk_match.group(5).strip(),
                lines=[]
            )
            i += 1
            continue
        
        # Hunk content lines
        if current_hunk is not None:
            if line.startswith('+') or line.startswith('-') or line.startswith(' ') or line == '':
                current_hunk.lines.append(line)
            elif line.startswith('\\'):
                # "\ No newline at end of file"
                current_hunk.lines.append(line)
            # If line doesn't match any pattern, it might be the start of a new diff
            # Let the loop continue to catch it
        
        i += 1
    
    # Don't forget the last file and hunk
    if current_file is not None:
        if current_hunk is not None:
            current_file.hunks.append(current_hunk)
        files.append(current_file)
    
    return files


# =============================================================================
# Language-Specific Test Pattern Detection
# =============================================================================

# Patterns that indicate test code for each language
# Each pattern is a tuple of (compiled_regex, weight)
# Weight indicates confidence (higher = more confident it's test code)

RUST_TEST_PATTERNS = [
    (re.compile(r'#\[cfg\(test\)\]'), 1.0),           # Test module marker
    (re.compile(r'#\[test\]'), 0.95),                  # Test function attribute
    (re.compile(r'#\[tokio::test\]'), 0.95),           # Async test attribute
    (re.compile(r'#\[async_std::test\]'), 0.95),       # Async test attribute
    (re.compile(r'mod\s+tests?\s*\{'), 0.9),           # Test module declaration
    (re.compile(r'use\s+super::\*;'), 0.7),            # Common in test modules
    (re.compile(r'assert(_eq|_ne|_matches)?!'), 0.6), # Assert macros
    (re.compile(r'#\[should_panic'), 0.9),             # Expected panic test
    (re.compile(r'#\[ignore\]'), 0.8),                 # Ignored test
    (re.compile(r'proptest!'), 0.9),                   # Property testing
    (re.compile(r'quickcheck!'), 0.9),                 # QuickCheck testing
]

PYTHON_TEST_PATTERNS = [
    (re.compile(r'^\s*>>>\s'), 0.95),                  # Doctest prompt
    (re.compile(r'doctest\.'), 0.9),                   # Doctest module reference
    (re.compile(r'def\s+test_\w+'), 0.85),             # Test function
    (re.compile(r'class\s+Test\w+'), 0.85),            # Test class
    (re.compile(r'@pytest\.(mark\.)?'), 0.9),          # Pytest decorators
    (re.compile(r'self\.assert\w+'), 0.7),             # Unittest assertions
    (re.compile(r'assert\s+.+'), 0.5),                 # Plain assert
    (re.compile(r'@unittest\.'), 0.85),                # Unittest decorators
    (re.compile(r'from\s+unittest\s+import'), 0.8),    # Unittest import
    (re.compile(r'import\s+pytest'), 0.8),             # Pytest import
    (re.compile(r'@mock\.'), 0.7),                     # Mock decorators
    (re.compile(r'@patch'), 0.7),                      # Patch decorator
]

GO_TEST_PATTERNS = [
    (re.compile(r'func\s+Test\w+\s*\('), 0.95),        # Test function
    (re.compile(r'func\s+Benchmark\w+\s*\('), 0.9),    # Benchmark function
    (re.compile(r'func\s+Example\w*\s*\('), 0.9),      # Example function
    (re.compile(r't\.Run\s*\('), 0.85),                # Subtests
    (re.compile(r't\.(Error|Fatal|Skip|Log)'), 0.8),   # Test helper calls
    (re.compile(r'testing\.T\b'), 0.7),                # Testing type reference
    (re.compile(r'testing\.B\b'), 0.7),                # Benchmark type reference
]

JAVA_TEST_PATTERNS = [
    (re.compile(r'@Test\b'), 0.95),                    # JUnit @Test
    (re.compile(r'@Before\b'), 0.9),                   # JUnit lifecycle
    (re.compile(r'@After\b'), 0.9),                    # JUnit lifecycle
    (re.compile(r'@BeforeEach\b'), 0.9),               # JUnit 5 lifecycle
    (re.compile(r'@AfterEach\b'), 0.9),                # JUnit 5 lifecycle
    (re.compile(r'@BeforeAll\b'), 0.9),                # JUnit 5 lifecycle
    (re.compile(r'@AfterAll\b'), 0.9),                 # JUnit 5 lifecycle
    (re.compile(r'@ParameterizedTest\b'), 0.95),       # Parameterized test
    (re.compile(r'@DisplayName\b'), 0.85),             # Test display name
    (re.compile(r'assertEquals\s*\('), 0.7),           # Assertion
    (re.compile(r'assertThat\s*\('), 0.7),             # AssertJ/Hamcrest
    (re.compile(r'verify\s*\('), 0.6),                 # Mockito verify
    (re.compile(r'when\s*\(.*\)\.then'), 0.6),         # Mockito stubbing
]

KOTLIN_TEST_PATTERNS = [
    (re.compile(r'@Test\b'), 0.95),                    # JUnit @Test
    (re.compile(r'@BeforeTest\b'), 0.9),               # Kotlin test lifecycle
    (re.compile(r'@AfterTest\b'), 0.9),                # Kotlin test lifecycle
    (re.compile(r'assertEquals\s*\('), 0.7),           # Assertion
    (re.compile(r'assertThat\s*\('), 0.7),             # AssertJ
    (re.compile(r'shouldBe\b'), 0.8),                  # Kotest matcher
    (re.compile(r'should\s*\{'), 0.8),                 # Kotest should block
]

JAVASCRIPT_TEST_PATTERNS = [
    (re.compile(r'\bdescribe\s*\('), 0.9),             # Test suite
    (re.compile(r'\bit\s*\('), 0.85),                  # Test case
    (re.compile(r'\btest\s*\('), 0.85),                # Jest test
    (re.compile(r'\bexpect\s*\('), 0.8),               # Assertion
    (re.compile(r'\bbeforeEach\s*\('), 0.85),          # Lifecycle hook
    (re.compile(r'\bafterEach\s*\('), 0.85),           # Lifecycle hook
    (re.compile(r'\bbeforeAll\s*\('), 0.85),           # Lifecycle hook
    (re.compile(r'\bafterAll\s*\('), 0.85),            # Lifecycle hook
    (re.compile(r'\bjest\.'), 0.9),                    # Jest reference
    (re.compile(r'\bsinon\.'), 0.8),                   # Sinon mocking
    (re.compile(r'\.toEqual\s*\('), 0.8),              # Jest matcher
    (re.compile(r'\.toBe\s*\('), 0.8),                 # Jest matcher
]

RUBY_TEST_PATTERNS = [
    (re.compile(r'\bdescribe\s+[\'"]'), 0.9),          # RSpec describe
    (re.compile(r'\bcontext\s+[\'"]'), 0.9),           # RSpec context
    (re.compile(r'\bit\s+[\'"]'), 0.85),               # RSpec example
    (re.compile(r'\bexpect\s*\('), 0.8),               # RSpec expectation
    (re.compile(r'\bshould\s+'), 0.7),                 # Old RSpec syntax
    (re.compile(r'\bdef\s+test_'), 0.9),               # Minitest method
    (re.compile(r'assert_equal\b'), 0.8),              # Minitest assertion
    (re.compile(r'assert_raises\b'), 0.8),             # Minitest assertion
    (re.compile(r'\bbefore\s*\{'), 0.8),               # RSpec before block
    (re.compile(r'\bafter\s*\{'), 0.8),                # RSpec after block
    (re.compile(r'\blet\s*\('), 0.7),                  # RSpec let
]

ELIXIR_TEST_PATTERNS = [
    (re.compile(r'\btest\s+"'), 0.95),                 # ExUnit test
    (re.compile(r'iex>'), 0.95),                       # Doctest prompt
    (re.compile(r'\bdescribe\s+"'), 0.9),              # ExUnit describe
    (re.compile(r'\bsetup\s+'), 0.85),                 # ExUnit setup
    (re.compile(r'\bassert\s+'), 0.7),                 # Assertion
    (re.compile(r'\brefute\s+'), 0.8),                 # Refutation
    (re.compile(r'@tag\s+'), 0.7),                     # Test tag
]

D_TEST_PATTERNS = [
    (re.compile(r'\bunittest\s*\{'), 0.95),            # D unittest block
    (re.compile(r'\bassert\s*\('), 0.6),               # D assert
]

# Map language names to their test patterns
LANGUAGE_TEST_PATTERNS: Dict[str, List[Tuple[re.Pattern, float]]] = {
    'rust': RUST_TEST_PATTERNS,
    'python': PYTHON_TEST_PATTERNS,
    'go': GO_TEST_PATTERNS,
    'java': JAVA_TEST_PATTERNS,
    'kotlin': KOTLIN_TEST_PATTERNS,
    'javascript': JAVASCRIPT_TEST_PATTERNS,
    'typescript': JAVASCRIPT_TEST_PATTERNS,  # Same patterns as JS
    'ruby': RUBY_TEST_PATTERNS,
    'elixir': ELIXIR_TEST_PATTERNS,
    'd': D_TEST_PATTERNS,
}

# Languages that commonly have inline tests (tests in same file as code)
INLINE_TEST_LANGUAGES = {'rust', 'python', 'go', 'elixir', 'd'}


def detect_language_from_filepath(filepath: str) -> Optional[str]:
    """
    Detect programming language from file extension.
    
    Args:
        filepath: File path to analyze
        
    Returns:
        Language name or None if unknown
    """
    ext_map = {
        '.rs': 'rust',
        '.py': 'python',
        '.go': 'go',
        '.java': 'java',
        '.kt': 'kotlin',
        '.kts': 'kotlin',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.rb': 'ruby',
        '.ex': 'elixir',
        '.exs': 'elixir',
        '.d': 'd',
        '.scala': 'scala',
    }
    
    filepath_lower = filepath.lower()
    for ext, lang in ext_map.items():
        if filepath_lower.endswith(ext):
            return lang
    
    return None


def is_test_filepath(filepath: str) -> bool:
    """
    Check whether a path should be treated as a test file.

    This is intentionally path-first (e.g. src/test, tests, __tests__) so that
    test fixtures and helper files in test directories are classified as tests
    even when individual hunks do not contain explicit test syntax.
    """
    filepath_lower = filepath.lower()

    test_path_patterns = [
        r'(^|/)src/test(/|$)',
        r'(^|/)tests?(/|$)',
        r'(^|/)spec(/|$)',
        r'(^|/)__tests__(/|$)',
        r'(^|/)testdata(/|$)',
        r'(^|/)test_data(/|$)',
        r'(^|/)fixtures?(/|$)',
    ]

    test_name_patterns = [
        r'_test\.[^/]+$',
        r'_spec\.[^/]+$',
        r'\.test\.[^/]+$',
        r'\.spec\.[^/]+$',
        r'test_[^/]+\.[^/]+$',
        r'[^/]*test\.java$',
        r'[^/]*tests\.java$',
        r'[^/]*it\.java$',
        r'[^/]*test\.kt$',
        r'[^/]*test\.scala$',
        r'[^/]*spec\.scala$',
    ]

    for pattern in test_path_patterns:
        if re.search(pattern, filepath_lower):
            return True

    for pattern in test_name_patterns:
        if re.search(pattern, filepath_lower):
            return True

    return False


def classify_hunk(
    hunk: DiffHunk,
    language: str,
    file_context: Optional[str] = None,
    logger: Optional[logging.Logger] = None
) -> DiffHunk:
    """
    Classify a single hunk as test or code based on its content.
    
    The classification considers:
    1. Language-specific test patterns in added/changed lines
    2. The hunk's context (function/class it's in)
    3. Surrounding context from the file
    
    Args:
        hunk: The DiffHunk to classify
        language: Programming language of the file
        file_context: Optional additional context from the file
        logger: Optional logger instance
        
    Returns:
        The same DiffHunk with hunk_type and confidence set
    """
    patterns = LANGUAGE_TEST_PATTERNS.get(language, [])
    
    if not patterns:
        # Unknown language - can't classify
        hunk.hunk_type = HunkType.UNKNOWN
        hunk.confidence = 0.0
        return hunk
    
    # Get all content to analyze
    added_content = '\n'.join(hunk.get_added_lines())
    removed_content = '\n'.join(hunk.get_removed_lines())
    context_content = '\n'.join(hunk.get_context_lines())
    all_content = f"{added_content}\n{removed_content}\n{context_content}\n{hunk.context}"
    
    # Calculate test score based on pattern matches
    test_score = 0.0
    max_possible_score = 0.0
    matched_patterns = []
    
    for pattern, weight in patterns:
        max_possible_score += weight
        # Check in added lines first (highest priority)
        if pattern.search(added_content):
            test_score += weight
            matched_patterns.append((pattern.pattern, weight, 'added'))
        # Check in context (lower priority)
        elif pattern.search(context_content) or pattern.search(hunk.context):
            test_score += weight * 0.5
            matched_patterns.append((pattern.pattern, weight * 0.5, 'context'))
    
    # Normalize score
    if max_possible_score > 0:
        normalized_score = test_score / max_possible_score
    else:
        normalized_score = 0.0
    
    # Classify based on score thresholds
    if normalized_score >= 0.3:
        hunk.hunk_type = HunkType.TEST
        hunk.confidence = min(normalized_score * 2, 1.0)  # Scale up confidence
    elif normalized_score > 0.1:
        hunk.hunk_type = HunkType.MIXED
        hunk.confidence = normalized_score
    else:
        hunk.hunk_type = HunkType.CODE
        hunk.confidence = 1.0 - normalized_score
    
    if logger and logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            f"Hunk @@ {hunk.old_start},{hunk.old_count} @@: "
            f"type={hunk.hunk_type.value}, score={normalized_score:.2f}, "
            f"confidence={hunk.confidence:.2f}, patterns={len(matched_patterns)}"
        )
    
    return hunk


def classify_file_hunks(
    file_diff: FileDiff,
    language: Optional[str] = None,
    logger: Optional[logging.Logger] = None
) -> FileDiff:
    """
    Classify all hunks in a file diff.
    
    For languages with inline tests (like Rust), this enables separating
    test hunks from code hunks within the same file.
    
    Args:
        file_diff: The FileDiff to classify
        language: Programming language (auto-detected from filepath if None)
        logger: Optional logger instance
        
    Returns:
        The same FileDiff with all hunks classified
    """
    # Auto-detect language if not provided
    if language is None:
        language = detect_language_from_filepath(file_diff.filepath)

    # Path-level signal: files in test directories should default to TEST
    # unless content classification already marks them as TEST/MIXED.
    file_is_test_path = is_test_filepath(file_diff.filepath)
    
    if language is None:
        # Can't classify without knowing the language
        for hunk in file_diff.hunks:
            if file_is_test_path:
                hunk.hunk_type = HunkType.TEST
                hunk.confidence = 0.6
            else:
                hunk.hunk_type = HunkType.UNKNOWN
                hunk.confidence = 0.0
        return file_diff
    
    # Special handling for Rust: track if we're inside a #[cfg(test)] block
    if language == 'rust':
        _classify_rust_hunks_with_context(file_diff, logger)
    else:
        # Standard classification for other languages
        for hunk in file_diff.hunks:
            classify_hunk(hunk, language, logger=logger)
            if file_is_test_path and hunk.hunk_type in (HunkType.CODE, HunkType.UNKNOWN):
                # Default to TEST for test-path files when content-based signals are weak.
                hunk.hunk_type = HunkType.TEST
                hunk.confidence = max(hunk.confidence, 0.6)

    # Apply the same defaulting behavior to Rust after its specialized pass.
    if language == 'rust' and file_is_test_path:
        for hunk in file_diff.hunks:
            if hunk.hunk_type in (HunkType.CODE, HunkType.UNKNOWN):
                hunk.hunk_type = HunkType.TEST
                hunk.confidence = max(hunk.confidence, 0.6)
    
    return file_diff


def _classify_rust_hunks_with_context(
    file_diff: FileDiff,
    logger: Optional[logging.Logger] = None
) -> None:
    """
    Special classification for Rust files that tracks #[cfg(test)] context.
    
    In Rust, tests are typically in a `mod tests` block marked with `#[cfg(test)]`.
    This function uses the hunk header context to determine if we're inside a test module.
    
    The hunk header looks like: @@ -10,5 +10,7 @@ fn some_function() {
    The part after @@ is the function/module context, which tells us where we are.
    """
    # Patterns to detect test context
    test_context_patterns = [
        re.compile(r'\bmod\s+tests?\b', re.IGNORECASE),  # mod tests or mod test
        re.compile(r'\bfn\s+test_\w+', re.IGNORECASE),   # fn test_something
    ]
    
    # Patterns in added lines that indicate test code
    test_line_patterns = [
        re.compile(r'#\[test\]'),                         # Test attribute
        re.compile(r'#\[cfg\(test\)\]'),                  # Test cfg
        re.compile(r'#\[tokio::test\]'),                  # Async test
        re.compile(r'#\[async_std::test\]'),              # Async test
        re.compile(r'mod\s+tests?\s*\{'),                 # Test module opening
    ]
    
    for hunk in file_diff.hunks:
        # Check 1: Is the hunk header context inside a test module?
        context_is_test = any(
            pattern.search(hunk.context) for pattern in test_context_patterns
        )
        
        # Check 2: Do the ADDED lines contain test markers?
        added_content = '\n'.join(hunk.get_added_lines())
        added_has_test_markers = any(
            pattern.search(added_content) for pattern in test_line_patterns
        )
        
        # Check 3: Do the CHANGED lines (added or removed) contain test markers?
        removed_content = '\n'.join(hunk.get_removed_lines())
        changed_has_test_markers = any(
            pattern.search(added_content) or pattern.search(removed_content)
            for pattern in test_line_patterns
        )
        
        # Determine classification
        if context_is_test:
            # Hunk header says we're in a test module
            hunk.hunk_type = HunkType.TEST
            hunk.confidence = 0.95
            if logger and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Rust hunk classified as TEST (context: {hunk.context[:40]})")
        elif added_has_test_markers:
            # Added lines contain test markers (like #[test] attribute)
            hunk.hunk_type = HunkType.TEST
            hunk.confidence = 0.9
            if logger and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Rust hunk classified as TEST (test markers in added lines)")
        elif changed_has_test_markers:
            # Changes involve test infrastructure but we're not clearly in test context
            hunk.hunk_type = HunkType.MIXED
            hunk.confidence = 0.7
        else:
            # No test indicators - this is production code
            hunk.hunk_type = HunkType.CODE
            hunk.confidence = 0.9
            if logger and logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Rust hunk classified as CODE (context: {hunk.context[:40]})")


def classify_all_hunks(
    file_diffs: List[FileDiff],
    language: Optional[str] = None,
    logger: Optional[logging.Logger] = None
) -> List[FileDiff]:
    """
    Classify hunks in all file diffs.
    
    Args:
        file_diffs: List of FileDiff objects to classify
        language: Default language (per-file detection used if None)
        logger: Optional logger instance
        
    Returns:
        The same list with all hunks classified
    """
    for file_diff in file_diffs:
        # Determine language for this file
        file_lang = language or detect_language_from_filepath(file_diff.filepath)
        classify_file_hunks(file_diff, file_lang, logger)
    
    return file_diffs


# =============================================================================
# Patch Reconstruction
# =============================================================================

def reconstruct_patch(
    file_diffs: List[FileDiff],
    include_types: Set[HunkType],
    logger: Optional[logging.Logger] = None
) -> str:
    """
    Reconstruct a valid git patch from classified hunks.
    
    This creates a new patch containing only hunks of the specified types.
    Files with no matching hunks are excluded entirely.
    
    Args:
        file_diffs: List of classified FileDiff objects
        include_types: Set of HunkType values to include
        logger: Optional logger instance
        
    Returns:
        A valid git patch string
    """
    patch_parts = []
    
    for file_diff in file_diffs:
        # Get hunks matching the requested types
        matching_hunks = [h for h in file_diff.hunks if h.hunk_type in include_types]
        
        if not matching_hunks and not file_diff.is_binary:
            # Skip files with no matching hunks
            continue
        
        # For binary files, include if the file itself is classified appropriately
        # (Binary files can't be split by hunk)
        if file_diff.is_binary:
            # Include binary files in the patch if we're including CODE hunks
            # (This is a policy decision - binary files are typically "code")
            if HunkType.CODE in include_types or HunkType.UNKNOWN in include_types:
                patch_parts.append('\n'.join(file_diff.ordered_header_lines()))
            continue
        
        # Reconstruct file patch with only matching hunks
        # We need to recalculate line numbers if we're excluding some hunks
        file_patch = _reconstruct_file_patch(file_diff, matching_hunks, logger)
        if file_patch:
            patch_parts.append(file_patch)
    
    return '\n'.join(patch_parts)


def _reconstruct_file_patch(
    file_diff: FileDiff,
    hunks: List[DiffHunk],
    logger: Optional[logging.Logger] = None
) -> str:
    """
    Reconstruct a file patch with specific hunks.
    
    When including a subset of hunks, we need to adjust line numbers
    to account for changes made by excluded hunks.
    
    For simplicity, this implementation includes the original line numbers.
    The patch should still apply correctly if hunks are non-overlapping.
    """
    if not hunks:
        return ""
    
    lines = file_diff.ordered_header_lines()
    
    for hunk in hunks:
        lines.append(hunk.header)
        lines.extend(hunk.lines)
    
    return '\n'.join(lines)


def generate_test_patch_from_hunks(
    diff_content: str,
    language: Optional[str] = None,
    logger: Optional[logging.Logger] = None
) -> str:
    """
    Generate a test-only patch by analyzing hunks.
    
    This is the main entry point for hunk-level test patch generation.
    
    Args:
        diff_content: Raw git diff content
        language: Programming language (auto-detected per file if None)
        logger: Optional logger instance
        
    Returns:
        Git patch containing only test-related hunks
    """
    if not diff_content:
        return ""
    
    # Parse diff into files and hunks
    file_diffs = parse_diff(diff_content)
    
    if logger:
        logger.debug(f"Parsed {len(file_diffs)} files from diff")
    
    # Classify all hunks
    classify_all_hunks(file_diffs, language, logger)
    
    # Reconstruct patch with only test hunks
    # Include MIXED hunks in test patch (conservative approach)
    test_patch = reconstruct_patch(
        file_diffs,
        include_types={HunkType.TEST, HunkType.MIXED},
        logger=logger
    )
    
    if logger:
        test_hunk_count = sum(
            len([h for h in f.hunks if h.hunk_type in (HunkType.TEST, HunkType.MIXED)])
            for f in file_diffs
        )
        logger.info(f"Generated test patch with {test_hunk_count} hunks")
    
    return test_patch


def generate_code_patch_from_hunks(
    diff_content: str,
    language: Optional[str] = None,
    logger: Optional[logging.Logger] = None
) -> str:
    """
    Generate a code-only patch by analyzing hunks.
    
    This is the main entry point for hunk-level code patch generation.
    
    Args:
        diff_content: Raw git diff content
        language: Programming language (auto-detected per file if None)
        logger: Optional logger instance
        
    Returns:
        Git patch containing only code-related hunks
    """
    if not diff_content:
        return ""
    
    # Parse diff into files and hunks
    file_diffs = parse_diff(diff_content)
    
    if logger:
        logger.debug(f"Parsed {len(file_diffs)} files from diff")
    
    # Classify all hunks
    classify_all_hunks(file_diffs, language, logger)
    
    # Reconstruct patch with only code hunks
    # Include UNKNOWN hunks in code patch (default to code)
    code_patch = reconstruct_patch(
        file_diffs,
        include_types={HunkType.CODE, HunkType.UNKNOWN},
        logger=logger
    )
    
    if logger:
        code_hunk_count = sum(
            len([h for h in f.hunks if h.hunk_type in (HunkType.CODE, HunkType.UNKNOWN)])
            for f in file_diffs
        )
        logger.info(f"Generated code patch with {code_hunk_count} hunks")
    
    return code_patch


def get_patch_statistics(
    diff_content: str,
    language: Optional[str] = None,
    logger: Optional[logging.Logger] = None
) -> Dict[str, any]:
    """
    Get statistics about patch classification.
    
    Useful for debugging and understanding how the patch was split.
    
    Args:
        diff_content: Raw git diff content
        language: Programming language
        logger: Optional logger instance
        
    Returns:
        Dictionary with statistics
    """
    file_diffs = parse_diff(diff_content)
    classify_all_hunks(file_diffs, language, logger)
    
    stats = {
        'total_files': len(file_diffs),
        'total_hunks': sum(len(f.hunks) for f in file_diffs),
        'test_hunks': 0,
        'code_hunks': 0,
        'mixed_hunks': 0,
        'unknown_hunks': 0,
        'mixed_files': [],  # Files with both test and code hunks
        'test_only_files': [],
        'code_only_files': [],
        'binary_files': [],
    }
    
    for file_diff in file_diffs:
        if file_diff.is_binary:
            stats['binary_files'].append(file_diff.filepath)
            continue
        
        has_test = False
        has_code = False
        
        for hunk in file_diff.hunks:
            if hunk.hunk_type == HunkType.TEST:
                stats['test_hunks'] += 1
                has_test = True
            elif hunk.hunk_type == HunkType.CODE:
                stats['code_hunks'] += 1
                has_code = True
            elif hunk.hunk_type == HunkType.MIXED:
                stats['mixed_hunks'] += 1
                has_test = True
                has_code = True
            else:
                stats['unknown_hunks'] += 1
                has_code = True  # Default unknown to code
        
        if has_test and has_code:
            stats['mixed_files'].append(file_diff.filepath)
        elif has_test:
            stats['test_only_files'].append(file_diff.filepath)
        elif has_code:
            stats['code_only_files'].append(file_diff.filepath)
    
    return stats
