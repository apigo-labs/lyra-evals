"""Opt-in official benchmark acceptance using no model credentials or inference."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vohu_evals.suites.adapters import grade_livecodebench, grade_text
from vohu_evals.suites.packs import agent_input, load_pack

ROOT = Path(__file__).resolve().parents[1]


async def main():
    for suite, count in {
        "ifeval": 541,
        "gpqa": 198,
        "livecodebench": 1055,
        "tau2": 278,
        "swebench": 500,
    }.items():
        manifest, rows = load_pack(ROOT / ".local/suites", suite)
        assert manifest["count"] == count
        public = agent_input(suite, rows[0])
        assert not {
            "expected",
            "private_test_cases",
            "private_task",
            "patch",
            "test_patch",
            "grader_sha256",
        } & set(public)
        if suite == "ifeval":
            result = grade_text(suite, rows[0], "", ROOT)
            assert not result["correct"]
        if suite == "gpqa":
            assert grade_text(suite, rows[0], "Answer: " + rows[0]["expected"], ROOT)["correct"]
        print(f"{suite}: frozen data and public projection verified ({count} cases)")
    row = {
        "public_test_cases": json.dumps([{"input": "1\n", "output": "2\n", "testtype": "stdin"}]),
        "private_test_cases": "[]",
        "metadata": "{}",
    }
    passed = await grade_livecodebench(row, "print(int(input())+1)")
    failed = await grade_livecodebench(row, "print(0)")
    assert passed["correct"] and not failed["correct"]
    print("Official LCB grader: correct/incorrect synthetic programs verified in offline Docker")


if __name__ == "__main__":
    asyncio.run(main())
