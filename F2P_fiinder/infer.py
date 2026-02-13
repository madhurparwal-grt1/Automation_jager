from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .diff_utils import (
    extract_changed_files,
    extract_changed_modules,
    extract_changed_symbols,
    likely_test_file,
    module_symbol_link_strength,
    parse_patch,
)
from .language_rules import (
    extract_test_ids,
    has_assertion_change,
    has_expected_change,
    normalize_language,
)
from .models import InferenceResult, TestCandidate


SIGNAL_WEIGHTS = {
    "NEW_TEST_FILE": 0.70,
    "NEW_TEST_CASE": 0.70,
    "ASSERTION_CHANGED": 0.45,
    "EXPECTED_VALUE_CHANGED": 0.45,
    "TEST_LOGIC_CHANGED": 0.20,
    "SYMBOL_LINK_STRONG": 0.30,
    "MODULE_LINK": 0.15,
    "REFACTOR_ONLY": -0.60,
}


def _confidence_from_score(score: float) -> str:
    if score >= 0.80:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def _default_test_id(file_path: str, hunk_index: int) -> str:
    return f"{file_path}::hunk_{hunk_index + 1}"


def _candidate_key(test_id: str, file_path: str) -> str:
    return f"{test_id}@@{file_path}"


def _collect_test_candidates(test_patch: str, language: str) -> Dict[str, TestCandidate]:
    lang = normalize_language(language)
    files = parse_patch(test_patch)
    candidates: Dict[str, TestCandidate] = {}

    for file_patch in files:
        if file_patch.is_deleted_file:
            continue
        treat_as_test = likely_test_file(file_patch.path) or bool(file_patch.hunks)
        if not treat_as_test:
            continue

        for idx, hunk in enumerate(file_patch.hunks):
            added = hunk.added_lines()
            removed = hunk.removed_lines()
            changed_lines = added + removed
            if not changed_lines:
                continue

            test_ids: Set[str] = set()
            for line in added:
                test_ids |= extract_test_ids(lang, file_patch.path, line)

            if not test_ids:
                test_ids.add(_default_test_id(file_patch.path, idx))

            for test_id in test_ids:
                key = _candidate_key(test_id, file_patch.path)
                if key not in candidates:
                    candidates[key] = TestCandidate(test_id=test_id, file_path=file_patch.path, hunk_index=idx)
                c = candidates[key]

                if file_patch.is_new_file:
                    c.signals.add("NEW_TEST_FILE")
                if test_id.startswith(file_patch.path + "::") and "hunk_" not in test_id:
                    c.signals.add("NEW_TEST_CASE")

                if any(has_assertion_change(lang, line) for line in changed_lines):
                    c.signals.add("ASSERTION_CHANGED")
                if any(has_expected_change(line) for line in changed_lines):
                    c.signals.add("EXPECTED_VALUE_CHANGED")

                non_trivial = [ln for ln in changed_lines if ln.strip() and not ln.strip().startswith(("#", "//", "/*", "*"))]
                if non_trivial:
                    c.signals.add("TEST_LOGIC_CHANGED")

    return candidates


def _score_candidates(
    candidates: Dict[str, TestCandidate],
    changed_symbols: Set[str],
    changed_modules: Set[str],
) -> None:
    for candidate in candidates.values():
        score = 0.0
        for signal in candidate.signals:
            score += SIGNAL_WEIGHTS.get(signal, 0.0)

        strong_link, weak_link = module_symbol_link_strength(
            test_id=candidate.test_id,
            file_path=candidate.file_path,
            changed_symbols=changed_symbols,
            changed_modules=changed_modules,
        )
        if strong_link:
            candidate.signals.add("SYMBOL_LINK_STRONG")
            score += SIGNAL_WEIGHTS["SYMBOL_LINK_STRONG"]
        if weak_link:
            candidate.signals.add("MODULE_LINK")
            score += SIGNAL_WEIGHTS["MODULE_LINK"]

        candidate.score = max(0.0, min(1.5, score))
        candidate.confidence = _confidence_from_score(candidate.score)


def infer_from_patches(
    full_patch: str,
    test_patch: str,
    code_patch: str,
    language: str,
) -> InferenceResult:
    lang = normalize_language(language)

    changed_files = extract_changed_files(full_patch)
    changed_modules = extract_changed_modules(changed_files)
    changed_symbols = extract_changed_symbols(code_patch if code_patch.strip() else full_patch)

    candidates = _collect_test_candidates(test_patch, lang)
    _score_candidates(candidates, changed_symbols, changed_modules)

    f2p: Set[str] = set()
    relevant: Set[str] = set()
    confidence: Dict[str, str] = {}
    signals: Dict[str, List[str]] = {}
    scores: Dict[str, float] = {}

    for candidate in candidates.values():
        tid = candidate.test_id
        scores[tid] = max(scores.get(tid, 0.0), round(candidate.score, 3))
        signals[tid] = sorted(set(signals.get(tid, []) + list(candidate.signals)))

        if candidate.score >= 0.70:
            f2p.add(tid)
            confidence[tid] = candidate.confidence
            relevant.add(tid)
        elif candidate.score >= 0.35:
            relevant.add(tid)
            confidence.setdefault(tid, candidate.confidence)

    p2p = relevant - f2p

    meta = {
        "mode": "patch_only",
        "language": lang,
        "candidate_count": len(candidates),
        "changed_file_count": len(changed_files),
        "changed_symbol_count": len(changed_symbols),
        "changed_module_count": len(changed_modules),
        "confidence": confidence,
        "signals": signals,
        "scores": scores,
        "notes": [
            "Predicted from patch only.",
            "No docker build and no runtime test execution used.",
        ],
    }

    return InferenceResult(
        fail_to_pass_predicted=sorted(f2p),
        pass_to_pass_predicted=sorted(p2p),
        meta=meta,
    )


def infer_from_patch_files(
    full_patch_path: str | Path,
    test_patch_path: str | Path,
    code_patch_path: str | Path,
    language: str,
) -> InferenceResult:
    full_patch = Path(full_patch_path).read_text(encoding="utf-8", errors="replace")
    test_patch = Path(test_patch_path).read_text(encoding="utf-8", errors="replace")
    code_patch = Path(code_patch_path).read_text(encoding="utf-8", errors="replace")
    return infer_from_patches(full_patch, test_patch, code_patch, language)


def to_json_dict(result: InferenceResult) -> Dict[str, object]:
    return {
        "FAIL_TO_PASS_PREDICTED": result.fail_to_pass_predicted,
        "PASS_TO_PASS_PREDICTED": result.pass_to_pass_predicted,
        "meta": result.meta,
    }


def to_json(result: InferenceResult) -> str:
    return json.dumps(to_json_dict(result), indent=2, ensure_ascii=True)
