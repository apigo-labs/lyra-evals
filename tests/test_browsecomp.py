from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from vohu_evals.benchmark import load_benchmark
from vohu_evals.judge import JudgeResult
from vohu_evals.models import Case, InvocationResult

ROOT = Path(__file__).resolve().parents[1]


def _encrypt(value: str, password: str) -> str:
    raw = value.encode()
    key = hashlib.sha256(password.encode()).digest()
    expanded = key * (len(raw) // len(key)) + key[: len(raw) % len(key)]
    return base64.b64encode(bytes(a ^ b for a, b in zip(raw, expanded, strict=True))).decode()


class FakeJudge:
    model = "judge-fixture"

    def __init__(self, output: str) -> None:
        self.output = output

    def evaluate(self, prompt: str) -> JudgeResult:
        assert "[correct_answer]: beta" in prompt
        return JudgeResult(self.output, "judge-request", self.model, {"total_tokens": 3}, 4)


class UnexpectedJudge:
    model = "judge-fixture"

    def evaluate(self, prompt: str) -> JudgeResult:
        raise AssertionError("strict exact answers must not call the judge")


def test_browsecomp_decrypt_matches_official_xor_contract() -> None:
    benchmark = load_benchmark(ROOT, "browsecomp")
    encrypted = _encrypt("question", "canary")
    assert benchmark.decrypt(encrypted, "canary") == "question"  # type: ignore[attr-defined]


def test_browsecomp_uses_intended_regex_group_and_defaults_missing_verdict_to_no() -> None:
    benchmark = load_benchmark(ROOT, "browsecomp")
    case = Case("1", "question", "beta", {"official_evaluator": True})
    result = InvocationResult(
        output_text="Exact Answer: beta alias",
        raw_response={},
        request_id="request",
        execution_id="execution",
        response_model="vohu/research-benchmark@v1",
        attempt_models=("glm-5.2", "glm-5.1", "minimax-m3"),
        usage={"total_tokens": 1},
        cost_usd=0,
        latency_ms=1,
    )
    benchmark.bind_judge(FakeJudge("correct: yes"))
    assert benchmark.score_case(case, result.output_text, result).correct is True
    benchmark.bind_judge(FakeJudge("yes"))
    assert benchmark.score_case(case, result.output_text, result).correct is False


def test_browsecomp_uses_final_anchored_judge_verdict() -> None:
    benchmark = load_benchmark(ROOT, "browsecomp")
    case = Case("1", "question", "beta", {"official_evaluator": True})
    result = InvocationResult(
        output_text="Exact Answer: beta alias",
        raw_response={},
        request_id="request",
        execution_id="execution",
        response_model="vohu/research-benchmark@v1",
        attempt_models=("glm-5.2", "kimi-k3", "minimax-m3"),
        usage={"total_tokens": 1},
        cost_usd=0,
        latency_ms=1,
    )
    benchmark.bind_judge(
        FakeJudge(
            "reasoning: the phrase incorrect: no is not the verdict field.\n"
            "correct: yes\n"
            "confidence: 90"
        )
    )

    score = benchmark.score_case(case, result.output_text, result)

    assert score.correct is True
    assert score.details["judge_verdict"] == "yes"
    assert score.details["judge_verdict_field_count"] == 1
    assert (
        score.details["judge_output_sha256"]
        == "22fe4de7c19644e1b933c00b13532026d0df489c79a724f57a89c21d7acb9450"
    )


def test_browsecomp_scores_strict_exact_answer_without_judge() -> None:
    benchmark = load_benchmark(ROOT, "browsecomp")
    case = Case("1", "question", "beta", {"official_evaluator": True})
    result = InvocationResult(
        output_text="Explanation: evidence\nExact Answer: beta\nConfidence: 90%",
        raw_response={},
        request_id="request",
        execution_id="execution",
        response_model="vohu/research-benchmark@v1",
        attempt_models=("glm-5.2", "kimi-k3", "minimax-m3"),
        usage={"total_tokens": 1},
        cost_usd=0,
        latency_ms=1,
    )
    benchmark.bind_judge(UnexpectedJudge())

    score = benchmark.score_case(case, result.output_text, result)

    assert score.correct is True
    assert score.details == {
        "evaluator_requests": 0,
        "grading_method": "strict_exact_answer",
        "judge_parser_version": "final-anchored-v2",
    }


def test_browsecomp_aggregate_audits_evaluator_calls_and_tokens() -> None:
    benchmark = load_benchmark(ROOT, "browsecomp")
    scores = [
        benchmark.score_case(
            Case("fixture", "q", "a"),
            "a",
            InvocationResult("a", {}, "r", "e", "m", ("qwen-fixture",), {}, 0, 1),
        )
    ]
    scores.append(
        type(scores[0])(
            1.0,
            True,
            "accuracy",
            {"evaluator_requests": 1, "judge_usage": {"total_tokens": 7}},
        )
    )

    assert benchmark.aggregate(scores) == {
        "metric": "accuracy",
        "score": 1.0,
        "cases": 2,
        "evaluator_requests": 1,
        "evaluator_total_tokens": 7,
    }
