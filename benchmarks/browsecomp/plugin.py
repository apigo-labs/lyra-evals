from __future__ import annotations

import base64
import csv
import hashlib
import random
import re
from pathlib import Path
from typing import Any

from vohu_evals.benchmark import Benchmark, DatasetNotReadyError
from vohu_evals.dataset import DatasetCache, DatasetIntegrityError
from vohu_evals.judge import JudgePort
from vohu_evals.models import Case, CaseScore, InvocationResult, RunStage

QUERY_TEMPLATE = """
{Question}

Your response should be in the following format:
Explanation: {{your explanation for your final answer}}
Exact Answer: {{your succinct, final answer}}
Confidence: {{your confidence score between 0% and 100% for your answer}}
""".strip()

GRADER_TEMPLATE = r"""
Judge whether the following [response] to [question] is correct or not based on the precise and unambiguous [correct_answer] below.

[question]: {question}

[response]: {response}

Your judgement must be in the format and criteria specified below:

extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_answer]: {correct_answer}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_answer], focusing only on if there are meaningful differences between [correct_answer] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_answer], focus only on whether the answers match.

correct: Answer 'yes' if extracted_final_answer matches the [correct_answer] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.


confidence: The extracted confidence score between 0|\%| and 100|\%| from [response]. Put 100 if there is no confidence score available.
""".strip()

JUDGE_PARSER_VERSION = "final-anchored-v2"


def derive_key(password: str, length: int) -> bytes:
    key = hashlib.sha256(password.encode()).digest()
    return key * (length // len(key)) + key[: length % len(key)]


def decrypt(ciphertext_b64: str, password: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64)
    return bytes(
        a ^ b for a, b in zip(encrypted, derive_key(password, len(encrypted)), strict=True)
    ).decode()


class Plugin(Benchmark):
    decrypt = staticmethod(decrypt)

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._judge: JudgePort | None = None

    def bind_judge(self, judge: JudgePort | None) -> None:
        self._judge = judge

    def evaluator_requests_per_case(self, case: Case) -> int:
        return int(bool(case.metadata.get("official_evaluator")))

    def evaluator_requests_for_response(self, case: Case, parsed_output: str) -> int:
        if not case.metadata.get("official_evaluator"):
            return 0
        return int(not self._strict_exact_answer(case, parsed_output))

    @staticmethod
    def _strict_exact_answer(case: Case, parsed_output: str) -> bool:
        exact_answers = re.findall(r"(?mi)^Exact Answer:\s*(.*?)\s*$", parsed_output)
        return bool(exact_answers and exact_answers[-1].strip() == str(case.expected).strip())

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
        if snapshot.record_count != self.manifest["dataset"]["expected_count"]:
            raise DatasetNotReadyError("BrowseComp frozen dataset is not materialized and verified")
        with snapshot.artifacts[0].path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        stage_config = self.manifest["stages"][stage.value]
        sample_n = int(stage_config["sample_n"])
        selected = rows
        if sample_n < len(rows):
            selected = random.Random(int(stage_config["seed"])).sample(rows, sample_n)
        row_index = {id(row): index for index, row in enumerate(rows)}
        return [
            Case(
                case_id=str(row_index[id(row)]),
                prompt=QUERY_TEMPLATE.format(Question=decrypt(row["problem"], row["canary"])),
                expected=decrypt(row["answer"], row["canary"]),
                metadata={"official_evaluator": True},
            )
            for row in selected
        ]

    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        if not case.metadata.get("official_evaluator"):
            correct = parsed_output.strip() == str(case.expected).strip()
            return CaseScore(float(correct), correct, "accuracy_fixture")
        if self._judge is None:
            raise DatasetNotReadyError("BrowseComp requires a Gateway-backed judge")
        if self._strict_exact_answer(case, parsed_output):
            return CaseScore(
                value=1.0,
                correct=True,
                metric="accuracy",
                details={
                    "evaluator_requests": 0,
                    "grading_method": "strict_exact_answer",
                    "judge_parser_version": JUDGE_PARSER_VERSION,
                },
            )
        question = case.prompt.split("\n\nYour response should", 1)[0]
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
            value=float(correct),
            correct=correct,
            metric="accuracy",
            details={
                "evaluator_requests": 1,
                "judge_model": self._judge.model,
                "judge_request_id": verdict.request_id,
                "judge_response_model": verdict.response_model,
                "judge_usage": verdict.usage,
                "judge_latency_ms": verdict.latency_ms,
                "judge_verdict": label,
                "judge_verdict_field_count": len(matches),
                "judge_output_sha256": hashlib.sha256(verdict.output_text.encode()).hexdigest(),
                "grading_method": "gateway_judge",
                "judge_parser_version": JUDGE_PARSER_VERSION,
            },
        )

    def aggregate(self, scores: list[CaseScore]) -> dict[str, Any]:
        base = super().aggregate(scores)
        base["evaluator_requests"] = sum(
            int(score.details.get("evaluator_requests", 0)) for score in scores
        )
        base["evaluator_total_tokens"] = sum(
            int(score.details.get("judge_usage", {}).get("total_tokens", 0)) for score in scores
        )
        return base
