"""Offline checks for the repo-local Inspect task: no Docker, no network, no model calls."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from vohu_evals.suites.adapters import lcb_code
from vohu_evals.suites.packs import freeze_pack, livecodebench_prompt

inspect_task = pytest.importorskip("inspect_tasks.livecodebench_v6")

ROWS = [
    {
        "case_id": "1873_A",
        "question_content": "Sort three cards.",
        "starter_code": "",
        "public_test_cases": '[{"input": "1\\n", "output": "YES\\n", "testtype": "stdin"}]',
        "grader_sha256": "a" * 64,
    },
    {
        "case_id": "abc_B",
        "question_content": "Return the answer.",
        "starter_code": "class Solution:\n    def f(self): ...",
        "public_test_cases": '[{"input": "[]", "output": "0", "testtype": "functional"}]',
        "grader_sha256": "b" * 64,
        "difficulty": "hard",
    },
]


@pytest.fixture
def pack_root(tmp_path, monkeypatch):
    freeze_pack(tmp_path, "livecodebench", ROWS, {"release": "release_v6", "subset": "lite"})
    inspect_task._rows_cache.clear()
    monkeypatch.setitem(inspect_task._state, "suite_root", tmp_path)
    monkeypatch.setitem(inspect_task._state, "image", "test-image")
    yield tmp_path
    inspect_task._rows_cache.clear()


def test_dataset_reuses_the_acp_prompt_and_pack_digest(pack_root):
    manifest, dataset = inspect_task.build_dataset(pack_root)
    samples = list(dataset)
    assert [sample.id for sample in samples] == ["1873_A", "abc_B"]
    assert manifest["count"] == 2 and len(manifest["sha256"]) == 64

    for sample, row in zip(samples, ROWS, strict=True):
        expected = (
            row["question_content"]
            + "\nReturn only the Python solution, without Markdown fences.\n"
            + json.dumps(
                {
                    "starter_code": row["starter_code"],
                    "public_test_cases": row["public_test_cases"],
                }
            )
        )
        assert sample.input == expected
        assert sample.input == livecodebench_prompt(row)
        assert "grader_sha256" not in sample.input


def test_sample_metadata_carries_stratification_fields_when_present(pack_root):
    _, dataset = inspect_task.build_dataset(pack_root)
    first, second = list(dataset)
    assert first.metadata == {"suite": "livecodebench", "has_starter_code": False}
    assert second.metadata == {
        "suite": "livecodebench",
        "has_starter_code": True,
        "difficulty": "hard",
    }


def test_task_metadata_records_the_frozen_digest(pack_root):
    built = inspect_task.livecodebench_v6(suite_root=str(pack_root), grader_image="test-image")
    manifest, _ = inspect_task.build_dataset(pack_root)
    assert built.metadata["pack_sha256"] == manifest["sha256"]
    assert built.metadata["pack_count"] == 2
    assert built.metadata["release"] == "release_v6"
    assert built.metadata["grader_image"] == "test-image"
    assert len(built.dataset) == 2


def fake_state(sample_id: str, completion: str):
    return SimpleNamespace(sample_id=sample_id, output=SimpleNamespace(completion=completion))


def run_scorer(monkeypatch, grader, completion, sample_id="1873_A"):
    calls = []

    async def fake_grade(row, code, **kwargs):
        calls.append({"row": row, "code": code, **kwargs})
        return grader(row, code)

    monkeypatch.setattr(inspect_task, "grade_livecodebench", fake_grade)
    score_fn = inspect_task.livecodebench_official()
    result = asyncio.run(score_fn(fake_state(sample_id, completion), None))
    return result, calls


def test_scorer_marks_a_passing_solution_correct(pack_root, monkeypatch):
    score, calls = run_scorer(
        monkeypatch,
        lambda row, code: {"correct": True, "value": 1.0, "metric": "pass@1", "tests": 7},
        "```python\nprint(1)\n```",
    )
    assert score.value == "C"
    assert calls[0]["code"] == "print(1)" == lcb_code("```python\nprint(1)\n```")
    assert calls[0]["row"]["case_id"] == "1873_A"
    assert calls[0]["image"] == "test-image"
    assert "tests_run=7" in score.explanation
    assert "YES" not in score.explanation  # no hidden or public test content


def test_scorer_marks_a_failing_solution_incorrect(pack_root, monkeypatch):
    score, calls = run_scorer(
        monkeypatch,
        lambda row, code: {"correct": False, "value": 0.0, "metric": "pass@1", "tests": 7},
        "print(2)",
        sample_id="abc_B",
    )
    assert score.value == "I"
    assert calls[0]["code"] == "print(2)"  # unfenced answers pass through unchanged


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("docker"), RuntimeError("Official LCB grader failed"), TimeoutError()],
)
def test_grader_infrastructure_failures_raise_instead_of_scoring_zero(
    pack_root, monkeypatch, error
):
    def boom(row, code):
        raise error

    with pytest.raises(type(error)):
        run_scorer(monkeypatch, boom, "print(1)")
