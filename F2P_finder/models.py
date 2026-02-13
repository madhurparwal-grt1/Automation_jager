from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class Hunk:
    header: str
    lines: List[str] = field(default_factory=list)

    def added_lines(self) -> List[str]:
        return [line[1:] for line in self.lines if line.startswith("+") and not line.startswith("+++")]

    def removed_lines(self) -> List[str]:
        return [line[1:] for line in self.lines if line.startswith("-") and not line.startswith("---")]

    def context_lines(self) -> List[str]:
        return [line[1:] for line in self.lines if line.startswith(" ")]


@dataclass
class FilePatch:
    path: str
    header_lines: List[str] = field(default_factory=list)
    hunks: List[Hunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted_file: bool = False


@dataclass
class TestCandidate:
    test_id: str
    file_path: str
    hunk_index: int
    signals: Set[str] = field(default_factory=set)
    score: float = 0.0
    confidence: str = "low"


@dataclass
class InferenceResult:
    fail_to_pass_predicted: List[str]
    pass_to_pass_predicted: List[str]
    meta: Dict[str, object]
