from __future__ import annotations

from pathlib import Path

from vohu_evals.benchmark import load_benchmark
from vohu_evals.models import Case, InvocationResult

ROOT = Path(__file__).resolve().parents[1]


def test_frames_hash_split_is_stable_nested_and_disjoint() -> None:
    benchmark = load_benchmark(ROOT, "frames")
    ranked = benchmark.__class__.__module__
    assert "vohu_eval_plugin_frames" in ranked
    first = benchmark.ranked_indices(824, "vohu-frames-development-v1")  # type: ignore[attr-defined]
    second = benchmark.ranked_indices(824, "vohu-frames-development-v1")  # type: ignore[attr-defined]
    assert first == second
    assert len(set(first[:100]).intersection(first[100:200])) == 0


def test_frames_case_never_projects_gold_links_into_runtime_fields() -> None:
    benchmark = load_benchmark(ROOT, "frames")
    case = benchmark.case_from_row(  # type: ignore[attr-defined]
        7,
        {
            "Prompt": "Which entity satisfies both constraints?",
            "Answer": "Entity A",
            "reasoning_types": "Multiple constraints",
            "wiki_links": "['https://gold.example/answer']",
            "wikipedia_link_1": "https://gold.example/answer",
        },
        "dev",
    )
    assert case.expected == "Entity A"
    assert "gold.example" not in case.prompt
    assert "gold.example" not in str(case.metadata)
    assert case.metadata == {
        "official_evaluator": True,
        "development_split": "dev",
        "reasoning_type": "Multiple constraints",
    }


def test_frames_strict_exact_answer_avoids_judge() -> None:
    benchmark = load_benchmark(ROOT, "frames")
    case = Case("1", "question", "Entity A", {"official_evaluator": True})
    result = InvocationResult(
        "Exact Answer: Entity A\nExplanation: constraints match.",
        {},
        "request",
        "execution",
        "apigo/vohu-research",
        ("glm-5.2", "glm-5.1", "minimax-m3"),
        {},
        0,
        1,
    )
    score = benchmark.score_case(case, result.output_text, result)
    assert score.correct is True
    assert score.details["evaluator_requests"] == 0
