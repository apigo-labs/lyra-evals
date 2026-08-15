from __future__ import annotations

import json
from pathlib import Path

from vohu_evals.benchmark import load_benchmark
from vohu_evals.judge import JudgeResult
from vohu_evals.models import Case, InvocationResult

ROOT = Path(__file__).resolve().parents[1]


class SequenceJudge:
    model = "gemini-3.1-pro-preview"

    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = iter(verdicts)
        self.calls: list[tuple[str, str | None, float]] = []

    def evaluate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0,
        reasoning_effort: str | None = None,
    ) -> JudgeResult:
        self.calls.append((prompt, system_prompt, temperature))
        verdict = next(self.verdicts)
        return JudgeResult(
            json.dumps({"verdict": verdict, "justification": "fixture"}),
            f"judge-{len(self.calls)}",
            self.model,
            {"total_tokens": 5},
            2,
        )


def _result() -> InvocationResult:
    return InvocationResult(
        "Research response",
        {},
        "request",
        "execution",
        "vohu/research-benchmark@v1",
        ("glm-5.2", "glm-5.1", "minimax-m3"),
        {"total_tokens": 10},
        0,
        1,
        ({"url": "https://example.com"},),
    )


def test_draco_flattens_nested_criteria_without_committing_dataset() -> None:
    benchmark = load_benchmark(ROOT, "draco")
    criteria = benchmark.extract_criteria(  # type: ignore[attr-defined]
        {
            "sections": [
                {
                    "id": "outer",
                    "title": "Outer",
                    "criteria": [{"id": "c1", "weight": 2, "requirement": "X"}],
                    "sections": [
                        {
                            "id": "inner",
                            "title": "Inner",
                            "criteria": [{"id": "c2", "weight": -1, "requirement": "Y"}],
                        }
                    ],
                }
            ]
        }
    )

    assert [item["id"] for item in criteria] == ["c1", "c2"]
    assert criteria[1] == {
        "id": "c2",
        "section": "Inner",
        "section_id": "inner",
        "weight": -1.0,
        "requirement": "Y",
    }


def test_draco_scores_three_judge_runs_with_signed_weights() -> None:
    benchmark = load_benchmark(ROOT, "draco")
    case = Case(
        "task",
        "question",
        None,
        {
            "official_evaluator": True,
            "domain": "Research",
            "criteria": [
                {
                    "id": "positive",
                    "section": "S",
                    "section_id": "s",
                    "weight": 2,
                    "requirement": "Has X",
                },
                {
                    "id": "negative",
                    "section": "S",
                    "section_id": "s",
                    "weight": -1,
                    "requirement": "Has error",
                },
            ],
        },
    )
    judge = SequenceJudge(["MET", "UNMET", "MET", "MET", "UNMET", "UNMET"])
    benchmark.bind_judge(judge)

    score = benchmark.score_case(case, "Research response", _result())

    # Run scores are 100, 50 and 0 after signed-weight normalization and clipping.
    assert score.value == 0.5
    assert score.details["judge_runs_completed"] == 3
    assert score.details["evaluator_requests"] == 6
    assert score.details["evaluator_total_tokens"] == 30
    assert score.details["mean_normalized_percent"] == 50.0
    assert all(call[1] and "expert evaluator" in call[1] for call in judge.calls)
    assert all(call[2] == 0.2 for call in judge.calls)
    assert benchmark.evaluator_requests_per_case(case) == 6


def test_draco_aggregate_is_macro_average_over_fixed_tasks() -> None:
    benchmark = load_benchmark(ROOT, "draco")
    from vohu_evals.models import CaseScore

    metrics = benchmark.aggregate(
        [
            CaseScore(
                0.5,
                False,
                "draco_rubric_score",
                {
                    "domain": "A",
                    "evaluator_requests": 6,
                    "evaluator_total_tokens": 30,
                    "judge_runs_failed": 0,
                },
            ),
            CaseScore(
                1.0,
                True,
                "draco_rubric_score",
                {
                    "domain": "B",
                    "evaluator_requests": 3,
                    "evaluator_total_tokens": 15,
                    "judge_runs_failed": 1,
                },
            ),
        ]
    )

    assert metrics["score"] == 0.75
    assert metrics["score_percent"] == 75.0
    assert metrics["tasks"] == 2
    assert metrics["evaluator_requests"] == 9
    assert metrics["evaluator_total_tokens"] == 45
    assert metrics["tasks_with_judge_failures"] == 1
