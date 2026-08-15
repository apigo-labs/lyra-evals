from __future__ import annotations

from vohu_evals.benchmark import Benchmark
from vohu_evals.models import Case, CaseScore, InvocationResult


class Plugin(Benchmark):
    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        answer = parsed_output.strip().upper().rstrip(".")[-1:]
        expected = str(case.expected).upper()
        return CaseScore(
            value=float(answer == expected),
            correct=answer == expected,
            metric="accuracy",
            details={"parsed_answer": answer},
        )
