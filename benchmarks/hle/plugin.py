from __future__ import annotations

import re

from vohu_evals.benchmark import Benchmark
from vohu_evals.models import Case, CaseScore, InvocationResult


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


class Plugin(Benchmark):
    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        correct = _normalize(parsed_output) == _normalize(str(case.expected))
        return CaseScore(value=float(correct), correct=correct, metric="exact_match")
