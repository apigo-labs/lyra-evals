"""Official LiveCodeBench test runner, executed only inside the isolated grader."""

import contextlib
import json
import sys
import tempfile

from testing_util import run_test

request = json.load(sys.stdin)
with (
    tempfile.TemporaryFile(mode="w+") as capture,
    contextlib.redirect_stdout(capture),
    contextlib.redirect_stderr(capture),
):
    results, _ = run_test(request["sample"], test=request["code"], debug=False, timeout=6)
passed = bool(results) and all(value == 1 for value in results)
print(
    json.dumps(
        {"correct": bool(passed), "value": float(passed), "metric": "pass@1", "tests": len(results)}
    )
)
