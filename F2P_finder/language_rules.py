from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

from .diff_utils import normalize_test_id


LANGUAGE_ALIASES: Dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "golang": "go",
    "c++": "cpp",
    "kt": "kotlin",
}


def normalize_language(language: str) -> str:
    lang = language.strip().lower()
    return LANGUAGE_ALIASES.get(lang, lang)


ASSERTION_PATTERNS: Dict[str, List[re.Pattern[str]]] = {
    "python": [re.compile(r"\bassert\b"), re.compile(r"\bself\.assert\w+\b"), re.compile(r"\bpytest\.raises\b")],
    "javascript": [re.compile(r"\bexpect\s*\("), re.compile(r"\bassert\s*\("), re.compile(r"\bshould\b")],
    "typescript": [re.compile(r"\bexpect\s*\("), re.compile(r"\bassert\s*\("), re.compile(r"\bshould\b")],
    "go": [re.compile(r"\bt\.(?:Error|Fatal|Fail|Skip)\w*\b"), re.compile(r"\brequire\.\w+\b"), re.compile(r"\bassert\.\w+\b")],
    "rust": [re.compile(r"\bassert(?:_eq|_ne|_matches)?!\b"), re.compile(r"\bpanic!\b")],
    "java": [re.compile(r"\bassert\w+\s*\("), re.compile(r"\bverify\s*\(")],
    "kotlin": [re.compile(r"\bassert\w+\s*\("), re.compile(r"\bshould(?:Be|Throw)\b")],
    "ruby": [re.compile(r"\bexpect\s*\("), re.compile(r"\bassert(?:_|$)\w*\b"), re.compile(r"\braise_error\b")],
    "php": [re.compile(r"\$this->assert\w+\s*\("), re.compile(r"\bexpectException\s*\(")],
    "csharp": [re.compile(r"\bAssert\.\w+\s*\("), re.compile(r"\bShould\(\)\.")],
    "c": [re.compile(r"\bASSERT_[A-Z_]+\s*\("), re.compile(r"\bEXPECT_[A-Z_]+\s*\("), re.compile(r"\bck_assert\w*\s*\(")],
    "cpp": [re.compile(r"\bASSERT_[A-Z_]+\s*\("), re.compile(r"\bEXPECT_[A-Z_]+\s*\("), re.compile(r"\bREQUIRE\s*\(")],
    "elixir": [re.compile(r"\bassert\b"), re.compile(r"\brefute\b")],
    "d": [re.compile(r"\bassert\s*\(")],
}


EXPECTED_CHANGE_HINTS = [
    re.compile(r"\bexpected\b", re.IGNORECASE),
    re.compile(r"\btoBe\b"),
    re.compile(r"\btoEqual\b"),
    re.compile(r"\btoMatch\b"),
    re.compile(r"\bstatus(?:Code)?\b", re.IGNORECASE),
    re.compile(r"\bmessage\b", re.IGNORECASE),
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bexception\b", re.IGNORECASE),
]


def _extract_generic_strings(line: str) -> List[str]:
    matches = re.findall(r"['\"]([^'\"]{3,120})['\"]", line)
    return [m.strip() for m in matches if m.strip()]


def _java_like_class_from_path(file_path: str) -> str:
    path = file_path.replace("\\", "/")
    for marker in ("/src/test/java/", "/src/main/java/", "/src/test/kotlin/", "/src/main/kotlin/"):
        if marker in path:
            rel = path.split(marker, 1)[1]
            stem = str(Path(rel).with_suffix("")).replace("/", ".")
            return stem
    return Path(file_path).stem


def clean_java_test_name(test_name: str) -> str:
    if "#" in test_name:
        class_part, method_part = test_name.split("#", 1)
    else:
        return re.sub(r"\(\)$", "", test_name).strip()

    method_part = re.sub(r"^\[\d+\]\s*", "", method_part)
    method_part = re.sub(r"\[.*?\]", "", method_part)
    method_part = re.sub(r"\(\)$", "", method_part)
    if " : " in method_part:
        method_part = method_part.split(" : ")[0]
    elif ":" in method_part:
        method_part = method_part.split(":")[0]
    method_part = method_part.strip()

    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)$", method_part)
    if m:
        method = m.group(1)
    else:
        m2 = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*)", method_part)
        method = m2.group(1).rstrip("-") if m2 else "test"
        if not method or not re.match(r"^[a-zA-Z_]", method):
            method = "test"
    return f"{class_part}.{method}"


def canonicalize_test_name(language: str, test_name: str) -> str:
    lang = normalize_language(language)
    name = normalize_test_id(test_name)
    if lang == "java":
        return clean_java_test_name(name)
    if lang == "go":
        if "." in name and "/" not in name:
            parts = name.rsplit(".", 1)
            if len(parts) == 2:
                return f"{parts[0]}/{parts[1]}"
    if lang in {"javascript", "typescript"}:
        return name.replace(" > ", " ")
    return name


def extract_test_ids(language: str, file_path: str, line: str) -> Set[str]:
    lang = normalize_language(language)
    test_ids: Set[str] = set()
    stem = Path(file_path).stem
    clean = line.strip()

    if lang == "python":
        m = re.search(r"^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", clean)
        if m:
            test_ids.add(normalize_test_id(f"{file_path}::{m.group(1)}"))
    elif lang in {"javascript", "typescript"}:
        for m in re.finditer(r"\b(?:it|test)\s*\(\s*['\"]([^'\"]+)['\"]", clean):
            test_ids.add(normalize_test_id(m.group(1)))
    elif lang == "go":
        m = re.search(r"^\s*func\s+(Test[A-Za-z0-9_]+)\s*\(", clean)
        if m:
            test_ids.add(normalize_test_id(f"{file_path}/{m.group(1)}"))
    elif lang == "rust":
        m = re.search(r"^\s*fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", clean)
        if m and ("test" in m.group(1).lower() or stem == "tests"):
            test_ids.add(normalize_test_id(f"{file_path}::{m.group(1)}"))
    elif lang in {"java", "kotlin"}:
        class_name = _java_like_class_from_path(file_path)
        m = re.search(
            r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?(?:void|fun|[\w<>\[\],?]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            clean,
        )
        if m:
            method_name = m.group(1)
            method_lower = method_name.lower()
            likely_test_method = (
                method_lower.startswith(("test", "should", "when", "given", "it", "can", "must"))
                or "test" in method_lower
            )
            if likely_test_method and method_name not in {"if", "for", "while", "switch"}:
                test_ids.add(normalize_test_id(f"{class_name}.{method_name}"))
    elif lang == "ruby":
        m = re.search(r"^\s*def\s+(test_[A-Za-z0-9_]+)\b", clean)
        if m:
            test_ids.add(normalize_test_id(f"{stem}#{m.group(1)}"))
        for m in re.finditer(r"\bit\s+['\"]([^'\"]+)['\"]", clean):
            test_ids.add(normalize_test_id(m.group(1)))
    elif lang == "php":
        m = re.search(r"^\s*public\s+function\s+(test[A-Za-z0-9_]+)\s*\(", clean)
        if m:
            test_ids.add(normalize_test_id(f"{stem}::{m.group(1)}"))
    elif lang == "csharp":
        m = re.search(r"^\s*public\s+(?:async\s+)?(?:Task|void)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", clean)
        if m and "test" in m.group(1).lower():
            test_ids.add(normalize_test_id(f"{stem}.{m.group(1)}"))
    elif lang in {"c", "cpp"}:
        m = re.search(r"\bTEST(?:_F|_P)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", clean)
        if m:
            test_ids.add(normalize_test_id(f"{m.group(1)}.{m.group(2)}"))
    elif lang == "elixir":
        for m in re.finditer(r"\btest\s+\"([^\"]+)\"", clean):
            test_ids.add(normalize_test_id(m.group(1)))
    elif lang == "d":
        if "unittest" in clean:
            test_ids.add(normalize_test_id(f"{file_path}::unittest"))

    return test_ids


def has_assertion_change(language: str, line: str) -> bool:
    lang = normalize_language(language)
    pats = ASSERTION_PATTERNS.get(lang, [])
    return any(p.search(line) for p in pats)


def has_expected_change(line: str) -> bool:
    if not _extract_generic_strings(line):
        return False
    return any(p.search(line) for p in EXPECTED_CHANGE_HINTS)


def is_refactor_only_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith(("#", "//", "/*", "*", "*/")):
        return True
    if re.match(r"^(?:import|from\s+\S+\s+import|using|package|namespace)\b", stripped):
        return True
    if re.match(r"^(?:public|private|protected|internal)\s+(?:class|interface|enum)\b", stripped):
        return False
    return False
