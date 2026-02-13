from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import FilePatch, Hunk


_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$")


def parse_patch(patch_text: str) -> List[FilePatch]:
    if not patch_text.strip():
        return []

    files: List[FilePatch] = []
    current_file: Optional[FilePatch] = None
    current_hunk: Optional[Hunk] = None

    for line in patch_text.splitlines():
        match = _DIFF_HEADER.match(line)
        if match:
            if current_file is not None:
                if current_hunk is not None:
                    current_file.hunks.append(current_hunk)
                    current_hunk = None
                files.append(current_file)
            current_file = FilePatch(path=match.group(2), header_lines=[line])
            continue

        if current_file is None:
            continue

        if line.startswith("@@"):
            if current_hunk is not None:
                current_file.hunks.append(current_hunk)
            current_hunk = Hunk(header=line)
            continue

        if line.startswith("new file mode "):
            current_file.is_new_file = True
        elif line.startswith("deleted file mode "):
            current_file.is_deleted_file = True

        if current_hunk is not None:
            if line.startswith(("+", "-", " ", "\\")):
                current_hunk.lines.append(line)
        else:
            current_file.header_lines.append(line)

    if current_file is not None:
        if current_hunk is not None:
            current_file.hunks.append(current_hunk)
        files.append(current_file)

    return files


def extract_changed_files(patch_text: str) -> Set[str]:
    return {fp.path for fp in parse_patch(patch_text)}


def extract_changed_modules(file_paths: Set[str]) -> Set[str]:
    modules: Set[str] = set()
    for file_path in file_paths:
        path = Path(file_path)
        for part in path.parts[:-1]:
            low = part.lower()
            if low in {"src", "main", "test", "tests", "lib", "pkg", "internal", "app"}:
                continue
            modules.add(low)
        modules.add(path.stem.lower())
    return modules


def extract_changed_symbols(code_patch_text: str) -> Set[str]:
    files = parse_patch(code_patch_text)
    symbols: Set[str] = set()

    symbol_patterns = [
        re.compile(r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
        re.compile(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        re.compile(r"\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        re.compile(r"\b(?:public|private|protected|internal|static|final|virtual|override)\s+[\w<>\[\],\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    ]

    for file_patch in files:
        for hunk in file_patch.hunks:
            for line in hunk.added_lines() + hunk.removed_lines():
                if not line.strip():
                    continue
                for pattern in symbol_patterns:
                    for match in pattern.finditer(line):
                        token = match.group(1).strip()
                        if len(token) >= 3:
                            symbols.add(token.lower())

    return symbols


def likely_test_file(path: str) -> bool:
    low = path.lower()
    filename = Path(path).name.lower()
    if any(token in low for token in ["/test/", "/tests/", "/spec/", "__tests__", "/fixtures/", "/mocks/"]):
        return True
    return any(
        filename.endswith(suffix)
        for suffix in (
            "_test.py", "test_.py", ".test.js", ".spec.js", ".test.ts", ".spec.ts",
            "_test.go", "test.java", "tests.java", "test.kt", "_spec.rb", "_test.rb",
            "test.php", "tests.php", "test.cs",
        )
    )


def normalize_test_id(test_id: str) -> str:
    return " ".join(test_id.strip().split())


def module_symbol_link_strength(test_id: str, file_path: str, changed_symbols: Set[str], changed_modules: Set[str]) -> Tuple[bool, bool]:
    text = f"{test_id} {file_path}".lower()
    strong = any(sym in text for sym in changed_symbols if len(sym) >= 4)
    weak = any(mod in text for mod in changed_modules if len(mod) >= 4)
    return strong, weak
