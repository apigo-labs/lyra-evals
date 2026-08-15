from __future__ import annotations

import json
from typing import Any

import nltk

from instruction_following_eval import evaluation_lib
from vohu_evals.benchmark import Benchmark, DatasetNotReadyError
from vohu_evals.dataset import DatasetCache, DatasetIntegrityError
from vohu_evals.evaluator import EvaluatorResourceCache
from vohu_evals.models import Case, CaseScore, InvocationResult, RunStage


class Plugin(Benchmark):
    def enumerate_cases(self, stage: RunStage) -> list[Case]:
        if stage is RunStage.FIXTURE:
            return super().enumerate_cases(stage)
        self.validate(stage)
        cache_root = self.root.parents[1] / ".cache" / "datasets"
        try:
            snapshot = DatasetCache(cache_root).plan(self.manifest)
        except DatasetIntegrityError as exc:
            raise DatasetNotReadyError(str(exc)) from exc
        if snapshot.record_count != self.manifest["dataset"]["expected_count"]:
            raise DatasetNotReadyError("IFEval frozen dataset is not materialized and verified")
        path = snapshot.artifacts[0].path
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        expected_instructions = int(self.manifest["dataset"]["expected_instruction_count"])
        if sum(len(row["instruction_id_list"]) for row in rows) != expected_instructions:
            raise DatasetNotReadyError("IFEval instruction count does not match frozen manifest")
        limit = int(self.manifest["stages"][stage.value]["first_n"])
        return [
            Case(
                case_id=str(row["key"]),
                prompt=row["prompt"],
                expected=None,
                metadata={
                    "instruction_id_list": row["instruction_id_list"],
                    "kwargs": row["kwargs"],
                    "official_evaluator": True,
                },
            )
            for row in rows[:limit]
        ]

    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        if not case.metadata.get("official_evaluator"):
            return self._score_fixture(case, parsed_output)
        nltk_data = EvaluatorResourceCache(self.root.parents[1]).nltk_data_path(self.manifest)
        if not nltk_data.is_dir():
            raise DatasetNotReadyError("IFEval official evaluator resources are not materialized")
        nltk_data_text = str(nltk_data)
        if nltk_data_text not in nltk.data.path:
            nltk.data.path.insert(0, nltk_data_text)
        example = evaluation_lib.InputExample(
            key=int(case.case_id),
            instruction_id_list=case.metadata["instruction_id_list"],
            prompt=case.prompt,
            kwargs=case.metadata["kwargs"],
        )
        responses = {case.prompt: parsed_output}
        strict = evaluation_lib.test_instruction_following_strict(example, responses)
        loose = evaluation_lib.test_instruction_following_loose(example, responses)
        strict_count = sum(strict.follow_instruction_list)
        instruction_count = len(strict.follow_instruction_list)
        return CaseScore(
            value=strict_count / instruction_count,
            correct=strict.follow_all_instructions,
            metric="instruction_level_strict",
            details={
                "strict_instruction_list": strict.follow_instruction_list,
                "loose_instruction_list": loose.follow_instruction_list,
                "strict_prompt": strict.follow_all_instructions,
                "loose_prompt": loose.follow_all_instructions,
            },
        )

    def aggregate(self, scores: list[CaseScore]) -> dict[str, Any]:
        if any(score.metric.endswith("_fixture") for score in scores):
            return super().aggregate(scores)
        instruction_total = sum(
            len(score.details.get("strict_instruction_list", [])) for score in scores
        )
        strict_instruction_correct = sum(
            sum(score.details.get("strict_instruction_list", [])) for score in scores
        )
        loose_instruction_correct = sum(
            sum(score.details.get("loose_instruction_list", [])) for score in scores
        )
        prompt_total = len(scores)
        return {
            "prompt_level_strict": self._ratio(
                sum(bool(score.details.get("strict_prompt")) for score in scores), prompt_total
            ),
            "instruction_level_strict": self._ratio(strict_instruction_correct, instruction_total),
            "prompt_level_loose": self._ratio(
                sum(bool(score.details.get("loose_prompt")) for score in scores), prompt_total
            ),
            "instruction_level_loose": self._ratio(loose_instruction_correct, instruction_total),
            "prompts": prompt_total,
            "instructions": instruction_total,
        }

    @staticmethod
    def _score_fixture(case: Case, parsed_output: str) -> CaseScore:
        checks = case.metadata.get("checks", {})
        correct = True
        if "prefix" in checks:
            correct = correct and parsed_output.startswith(str(checks["prefix"]))
        if "max_words" in checks:
            correct = correct and len(parsed_output.split()) <= int(checks["max_words"])
        if "forbidden" in checks:
            correct = correct and str(checks["forbidden"]) not in parsed_output
        return CaseScore(
            value=float(correct),
            correct=correct,
            metric="instruction_level_strict_fixture",
            details={"checks": checks},
        )

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0
