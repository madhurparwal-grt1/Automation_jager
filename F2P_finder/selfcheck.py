from __future__ import annotations

import json

from .infer import infer_from_patches, to_json_dict


SAMPLE_FULL_PATCH = """
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,6 @@
 def add(a, b):
-    return a + b
+    if a is None:
+        raise ValueError("a required")
+    return a + b
diff --git a/tests/test_app.py b/tests/test_app.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/test_app.py
@@ -0,0 +1,8 @@
+import pytest
+
+def test_add_raises_for_none():
+    with pytest.raises(ValueError):
+        add(None, 1)
"""

SAMPLE_TEST_PATCH = """
diff --git a/tests/test_app.py b/tests/test_app.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/test_app.py
@@ -0,0 +1,8 @@
+import pytest
+
+def test_add_raises_for_none():
+    with pytest.raises(ValueError):
+        add(None, 1)
"""

SAMPLE_CODE_PATCH = """
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,6 @@
 def add(a, b):
-    return a + b
+    if a is None:
+        raise ValueError("a required")
+    return a + b
"""


def run_selfcheck() -> int:
    result = infer_from_patches(
        full_patch=SAMPLE_FULL_PATCH,
        test_patch=SAMPLE_TEST_PATCH,
        code_patch=SAMPLE_CODE_PATCH,
        language="python",
    )
    payload = to_json_dict(result)

    assert "FAIL_TO_PASS_PREDICTED" in payload
    assert "PASS_TO_PASS_PREDICTED" in payload
    assert len(payload["FAIL_TO_PASS_PREDICTED"]) >= 1
    assert isinstance(payload["meta"]["signals"], dict)

    print(json.dumps(payload, indent=2))
    print("SELF-CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_selfcheck())
