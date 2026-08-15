from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any

from vohu_evals.benchmark import Benchmark, DatasetNotReadyError
from vohu_evals.dataset import DatasetCache, DatasetIntegrityError
from vohu_evals.judge import JudgePort
from vohu_evals.models import Case, CaseScore, InvocationResult, RunStage

QUERY_TEMPLATE = """
{Question}

Your response must use this format:
Exact Answer: {{the shortest complete answer}}
Explanation: {{one concise sentence explaining the decisive constraints}}
""".strip()

GRADER_TEMPLATE = """
Decide whether the response gives the same answer as the reference. Accept harmless aliases,
formatting differences, and small numerical rounding only when they are semantically equivalent.
Do not solve the question or prefer a different answer.

[question]
{question}

[reference answer]
{correct_answer}

[response]
{response}

Return exactly one final line:
correct: yes
or
correct: no
""".strip()

JUDGE_PARSER_VERSION = "final-anchored-v1"


def ranked_indices(count: int, seed: str) -> list[int]:
    return sorted(
        range(count),
        key=lambda index: hashlib.sha256(f"{seed}:{index}".encode()).digest(),
    )


def case_from_row(index: int, row: dict[str, str], split: str) -> Case:
    return Case(
        case_id=str(index),
        prompt=QUERY_TEMPLATE.format(Question=row["Prompt"].strip()),
        expected=row["Answer"].strip(),
        metadata={
            "official_evaluator": True,
            "development_split": split,
            "reasoning_type": row.get("reasoning_types", "").strip(),
        },
    )


class Plugin(Benchmark):
    ranked_indices = staticmethod(ranked_indices)
    case_from_row = staticmethod(case_from_row)

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._judge: JudgePort | None = None

    def bind_judge(self, judge: JudgePort | None) -> None:
        self._judge = judge

    def evaluator_requests_per_case(self, case: Case) -> int:
        return int(bool(case.metadata.get("official_evaluator")))

    @staticmethod
    def _strict_exact_answer(case: Case, parsed_output: str) -> bool:
        exact = re.findall(r"(?mi)^Exact Answer:\s*(.*?)\s*$", parsed_output)
        return bool(exact and exact[-1].strip() == str(case.expected).strip())

    def enumerate_cases(self, stage: RunStage) -> list[Case]:
        if stage is RunStage.FIXTURE:
            return super().enumerate_cases(stage)
        self.validate(stage)
        try:
            snapshot = DatasetCache(self.root.parents[1] / ".cache" / "datasets").plan(
                self.manifest
            )
        except DatasetIntegrityError as exc:
            raise DatasetNotReadyError(str(exc)) from exc
        expected_count = int(self.manifest["dataset"]["expected_count"])
        if snapshot.record_count != expected_count:
            raise DatasetNotReadyError("FRAMES frozen dataset is not materialized and verified")
        with snapshot.artifacts[0].path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source, delimiter="\t"))
        split_policy = self.manifest["split_policy"]
        dev_n = int(split_policy["dev_n"])
        holdout_n = int(split_policy["holdout_n"])
        order = self.ranked_indices(len(rows), str(split_policy["seed"]))
        stage_config = self.manifest["stages"][stage.value]
        split = str(stage_config["split"])
        pool = order[:dev_n] if split == "dev" else order[dev_n : dev_n + holdout_n]
        selected = pool[: int(stage_config["sample_n"])]
        return [self.case_from_row(index, rows[index], split) for index in selected]

    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        if not case.metadata.get("official_evaluator"):
            correct = parsed_output.strip() == str(case.expected).strip()
            return CaseScore(float(correct), correct, "accuracy_fixture")
        if self._strict_exact_answer(case, parsed_output):
            return CaseScore(
                1.0,
                True,
                "accuracy",
                {"evaluator_requests": 0, "grading_method": "strict_exact_answer"},
            )
        if self._judge is None:
            raise DatasetNotReadyError("FRAMES requires a Gateway-backed judge")
        question = case.prompt.split("\n\nYour response must", 1)[0]
        verdict = self._judge.evaluate(
            GRADER_TEMPLATE.format(
                question=question,
                correct_answer=case.expected,
                response=parsed_output,
            )
        )
        matches = re.findall(r"(?mi)^correct:\s*(yes|no)\s*$", verdict.output_text)
        label = matches[-1].lower() if matches else "no"
        correct = label == "yes"
        return CaseScore(
            float(correct),
            correct,
            "accuracy",
            {
                "evaluator_requests": 1,
                "judge_model": self._judge.model,
                "judge_request_id": verdict.request_id,
                "judge_response_model": verdict.response_model,
                "judge_usage": verdict.usage,
                "judge_latency_ms": verdict.latency_ms,
                "judge_verdict": label,
                "judge_parser_version": JUDGE_PARSER_VERSION,
                "judge_output_sha256": hashlib.sha256(verdict.output_text.encode()).hexdigest(),
            },
        )

    def aggregate(self, scores: list[CaseScore]) -> dict[str, Any]:
        base = super().aggregate(scores)
        base["evaluator_requests"] = sum(
            int(score.details.get("evaluator_requests", 0)) for score in scores
        )
        return base
