"""Offline GPQA scoring fixture test: no model calls, saved answers only.

Exercises scripts/score_benchmark.py end to end against a small frozen GPQA pack,
covering the same closed-book answer-line parsing used by the live ACP harness.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_score_benchmark():
    spec = importlib.util.spec_from_file_location(
        "score_benchmark", REPO_ROOT / "scripts" / "score_benchmark.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def gpqa_fixture(tmp_path, monkeypatch):
    from vohu_evals.suites.packs import freeze_pack, gpqa_rows

    csv_path = tmp_path / "gpqa.csv"
    csv_path.write_text(
        "Question,Correct Answer,Incorrect Answer 1,Incorrect Answer 2,Incorrect Answer 3\n"
        '"Synthetic fixture question one?","right one","wrong a","wrong b","wrong c"\n'
        '"Synthetic fixture question two?","right two","wrong d","wrong e","wrong f"\n'
    )
    rows = gpqa_rows(csv_path, seed=7)

    module = _load_score_benchmark()
    suites_root = tmp_path / "suites"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    freeze_pack(suites_root, "gpqa", rows, {"revision": "fixture"})
    # dataset_root(ROOT / ".local/suites", ...) — point ".local/suites" at our fixture.
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "suites").symlink_to(suites_root)
    return module, rows


def test_gpqa_offline_scoring_end_to_end(tmp_path, gpqa_fixture, monkeypatch):
    module, rows = gpqa_fixture
    answers = {
        rows[0]["case_id"]: f"Working through it.\nAnswer: {rows[0]['expected']}",
        rows[1]["case_id"]: "Working through it.\nAnswer: Z",  # unparseable -> wrong
    }
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps(answers))
    output_path = tmp_path / "output.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score_benchmark.py",
            "gpqa",
            "--answers",
            str(answers_path),
            "--output",
            str(output_path),
        ],
    )
    module.main()

    result = json.loads(output_path.read_text())
    assert result["count"] == 2
    by_id = {r["case_id"]: r for r in result["results"]}
    assert by_id[rows[0]["case_id"]]["status"] == "scored"
    assert by_id[rows[0]["case_id"]]["correct"] is True
    assert by_id[rows[1]["case_id"]]["status"] == "scored"
    assert by_id[rows[1]["case_id"]]["correct"] is False
    assert by_id[rows[1]["case_id"]]["parsed_answer"] is None
    assert result["summary"]["correct"] == 1
    assert result["summary"]["metrics"]["accuracy"] == pytest.approx(0.5)


def test_gpqa_offline_scoring_requires_exact_case_coverage(tmp_path, gpqa_fixture, monkeypatch):
    module, rows = gpqa_fixture
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps({rows[0]["case_id"]: "Answer: A"}))
    output_path = tmp_path / "output.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score_benchmark.py",
            "gpqa",
            "--answers",
            str(answers_path),
            "--output",
            str(output_path),
        ],
    )
    with pytest.raises(ValueError):
        module.main()
