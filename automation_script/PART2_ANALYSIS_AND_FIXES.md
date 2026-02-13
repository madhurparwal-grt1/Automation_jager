# Part 2 Failure Analysis: 0 Pass-to-Pass / Fail-to-Pass

## Summary

Part 2 was failing or producing **0 FAIL_TO_PASS and 0 PASS_TO_PASS** due to:

1. **Root cause of 0/0**: BASE and/or PATCHED test runs never executed any tests (environment/setup failure), so `result.json` had empty `tests_passed` and `tests_failed`. The categorization logic is correct; the inputs had no test names.
2. **Code bugs** that caused Part 2 to crash or behave incorrectly:
   - Loading BASE result from a derived path instead of state’s `base_artifacts_dir` (risk of wrong/missing file).
   - `UnboundLocalError` for `json` in `test_results.categorize_tests` (local `import json` only in one branch).
   - `'str' object has no attribute 'rglob'` in cleanup when `workspace_root` was passed as a string.
   - No handling when `pr_result` is `None` (AttributeError when building `pr_result_dict`).

---

## 1. Why 0 Pass-to-Pass and Fail-to-Pass?

### What the script expects

- **FAIL_TO_PASS**: Tests that failed in BASE but pass in PR (fixes) or new tests in PR.
- **PASS_TO_PASS**: Tests that passed in both BASE and PR and are relevant to the patch.

Both lists are derived from:

- `base_result`: `tests_passed`, `tests_failed` from BASE run (`artifacts/base/result.json`).
- `pr_result`: same from PATCHED run (`artifacts/pr/result.json`).

### What actually happened (e.g. vercel-next.js)

- **BASE run**: Test command failed before any tests ran (e.g. `cross-env: not found`, `node_modules missing`). Container runner still wrote `result.json` with `tests_passed: []`, `tests_failed: []`.
- **PATCHED run**: Same environment issue → again 0 passed, 0 failed.

So:

- `base_passed`, `base_failed`, `pr_passed`, `pr_failed` are all empty.
- `all_passing_both = base_passed & pr_passed` = ∅.
- **FAIL_TO_PASS** = tests in `pr_passed` that are in `base_failed` or new → 0.
- **PASS_TO_PASS** = PR-relevant subset of `all_passing_both` → 0.

So **0/0 is the correct outcome** when no tests run; the bug is upstream (test command/env), not the categorization logic.

### Other case: missing BASE result (e.g. kubernetes)

- Error: `No such file or directory: '.../artifacts/base/result.json'`.
- Cause: Part 2 was loading BASE result as `workspace_path / ARTIFACTS_DIR / "base" / "result.json"`. If Part 2 is run with a different workspace path than Part 1, or Part 1 didn’t write that path, the file can be missing. Using the path stored in state avoids that.

---

## 2. Bugs Fixed

### 2.1 BASE result path (part2_patch_and_evaluate.py)

- **Before**: `base_result_file = workspace_path / ARTIFACTS_DIR / "base" / "result.json"`.
- **After**: `base_result_file = base_artifacts_dir / "result.json"` (from state).
- **Also**: Explicit check that `base_result_file.exists()`; if not, log a clear error and return 1.

This makes Part 2 robust to workspace path differences and gives a clear error when BASE result is missing.

### 2.2 UnboundLocalError for `json` (test_results.py)

- **Symptom**: `UnboundLocalError: cannot access local variable 'json' where it is not associated with a value` at the agent-log line that uses `json.dumps(...)`.
- **Cause**: `import json` existed only inside `if patch_content:`. Python treats `json` as a local name for the whole function, so using `json` before that block (or in a path where the import wasn’t run) triggers the error.
- **Fix**: Add `import json` at the top of `test_results.py` and remove the inner `import json` in the `if patch_content:` block. All uses of `json` now refer to the module.

### 2.3 `'str' object has no attribute 'rglob'` (cleanup.py)

- **Symptom**: In `cleanup_pycache`, `workspace_root.rglob("__pycache__")` raised because `workspace_root` was a `str`.
- **Fix**: At the start of `cleanup_pycache`, set `root = Path(workspace_root)` and use `root` for `rglob` and the rest of the function. Part 2 already passes a `Path`; the conversion guarantees safety if a string is ever passed.

### 2.4 `pr_result` is None (part2_patch_and_evaluate.py)

- **Symptom**: When `run_patched_tests` returned `None`, the code still built `pr_result_dict` from `pr_result.tests_passed` etc. → AttributeError.
- **Fix**:
  - If `pr_result is None`, set `pr_result_dict = None` and try to load from `pr_artifacts_dir / "result.json"` if it exists.
  - If no file, use an empty result dict so categorization and metadata still run with empty PR results.

### 2.5 Clear warning when no tests ran (part2_patch_and_evaluate.py)

- After categorization, if `base_ran == 0` or `pr_ran == 0` (no tests in BASE or PR results), log a warning that no tests were available to categorize and that this usually indicates a test-command or environment failure (e.g. missing deps, wrong command).

---

## 3. What to do when you still see 0/0

When Part 2 completes but FAIL_TO_PASS and PASS_TO_PASS are both 0:

1. **Check BASE and PATCHED result files**
   - `workspace/artifacts/base/result.json` and `workspace/artifacts/pr/result.json`.
   - Look at `tests_passed`, `tests_failed`, and `stdout`/`stderr` (or equivalent). If they’re empty and stderr shows command/env errors, the test run never executed tests.

2. **Fix the test environment**
   - Ensure the test command (e.g. `pnpm test`, `npm test`) is correct and that dependencies are installed in the image (e.g. `node_modules`, `cross-env`).
   - Use repo-specific config or Docker healing so that the image has the right runtime and dependencies for the detected test command.

3. **Use the new warning**
   - The new log line: “No test results to categorize: BASE ran X tests, PR ran Y tests…” confirms that 0/0 is due to no tests run, not a bug in categorization.

---

## 4. Files changed

| File | Changes |
|------|--------|
| `part2_patch_and_evaluate.py` | Use `base_artifacts_dir` for BASE result; check file exists; handle `pr_result is None` (load from file or empty dict); add warning when 0 tests ran; comment for cleanup Path. |
| `test_results.py` | Add top-level `import json`; remove inner `import json` in `if patch_content:` block. |
| `cleanup.py` | In `cleanup_pycache`, use `root = Path(workspace_root)` and use `root` for `rglob`. |

These changes make Part 2 robust to missing BASE result, PR result, and string workspace path, fix the `json` UnboundLocalError, and make 0/0 outcomes interpretable as “no tests ran” rather than a silent logic bug.
