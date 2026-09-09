from __future__ import annotations

from vohu_evals.benchmark import Benchmark
from vohu_evals.models import Case, CaseScore, InvocationResult
from vohu_evals.suites.packs import gpqa_score


class Plugin(Benchmark):
    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        score = gpqa_score(parsed_output, str(case.expected).upper())
        return CaseScore(
            value=score["value"],
            correct=score["correct"],
            metric="accuracy",
            details={"parsed_answer": score["parsed_answer"]},
        )
