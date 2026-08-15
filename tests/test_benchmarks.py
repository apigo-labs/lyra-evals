from __future__ import annotations

from pathlib import Path

import pytest

from vohu_evals.benchmark import DatasetNotReadyError, load_benchmark
from vohu_evals.evaluator import EvaluatorResourceCache
from vohu_evals.models import Case, InvocationResult, RunStage

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["gpqa", "hle", "ifeval", "browsecomp", "draco"])
def test_all_plugins_have_valid_fixtures(name: str) -> None:
    benchmark = load_benchmark(ROOT, name)
    cases = benchmark.enumerate_cases(RunStage.FIXTURE)
    assert cases
    assert all(case.fixture_response for case in cases)


@pytest.mark.parametrize("name", ["gpqa", "hle"])
def test_scaffold_blocks_publication(name: str) -> None:
    benchmark = load_benchmark(ROOT, name)
    with pytest.raises(DatasetNotReadyError, match="not publication-ready"):
        benchmark.validate(RunStage.PUBLICATION)


def test_ifeval_uses_official_evaluator_for_all_four_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nltk_data = tmp_path / "nltk_data"
    nltk_data.mkdir()
    monkeypatch.setattr(EvaluatorResourceCache, "nltk_data_path", lambda *_: nltk_data)
    benchmark = load_benchmark(ROOT, "ifeval")
    case = Case(
        case_id="3757",
        prompt="Choose one phrase.",
        expected=None,
        metadata={
            "official_evaluator": True,
            "instruction_id_list": ["detectable_format:constrained_response"],
            "kwargs": [{}],
        },
    )
    result = InvocationResult(
        output_text="My answer is yes.",
        raw_response={},
        request_id="request",
        execution_id="execution",
        response_model="apigo/vohu",
        attempt_models=("qwen-fixture",),
        usage={"input_tokens": 1, "output_tokens": 1},
        cost_usd=0,
        latency_ms=1,
    )

    score = benchmark.score_case(case, result.output_text, result)
    metrics = benchmark.aggregate([score])

    assert score.correct is True
    assert metrics == {
        "prompt_level_strict": 1.0,
        "instruction_level_strict": 1.0,
        "prompt_level_loose": 1.0,
        "instruction_level_loose": 1.0,
        "prompts": 1,
        "instructions": 1,
    }
