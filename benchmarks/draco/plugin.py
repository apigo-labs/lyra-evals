from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from vohu_evals.benchmark import Benchmark, DatasetNotReadyError
from vohu_evals.dataset import DatasetCache, DatasetIntegrityError
from vohu_evals.gateway import GatewayError
from vohu_evals.judge import JudgePort
from vohu_evals.models import Case, CaseScore, InvocationResult, RunStage

AGENT_PREFIX = """You are an agent. Keep going until the user's query is fully resolved. The only thing that ends your turn is emitting a final written analysis — if you stop without one, your work is wasted. When you hit uncertainty, make the most reasonable assumption, note it, and continue; do not ask the user.

You have a budget of 16 tool calls total — each web_search and each web_fetch counts as one. Plan to use ~10–12 for research and reserve the rest so you can still write the analysis if you exhaust the budget.

Your final analysis must be a substantive, well-organized written response addressing the query directly. It must not contain intermediate reasoning, search narration, or phrases like "Let me search for...".

---

"""

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator assessing an AI-generated research response against a single rubric criterion.

The criterion is a literal description of something to check for in the response. Your job is to decide whether that description is true of the response.

  MET    = the description in the criterion is true of the response (the property/statement/behaviour described IS present in the response).
  UNMET  = the description is NOT true of the response (the property/statement/behaviour described is NOT present).

Some criteria describe desirable properties (e.g. "States that X is the correct treatment"). Others describe errors or harmful content (e.g. "Recommends home management instead of ER"). In both cases, MET means the described content IS present; it does NOT mean "the response is good". A response that correctly avoids a described error should be UNMET on that criterion, not MET.

Be strict and objective. Base your verdict on specific evidence in the response, not on overall impressions of quality.

Return a single JSON object with these fields:
  "verdict"       — exactly "MET" or "UNMET"
  "justification" — 1-3 sentences citing specific evidence from the response

Return only the JSON object, no other text."""

JUDGE_USER_TEMPLATE = """## Task

{problem}

## Response Being Evaluated

{response}

## Criterion

{requirement}

## Instructions

Decide whether the criterion above is MET or UNMET in the response.
Return only a JSON object: {{"verdict": "MET" | "UNMET", "justification": "..."}}
"""


def _criteria(node: dict[str, Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for section in node.get("sections", []):
        if not isinstance(section, dict):
            continue
        _walk_criteria(
            section,
            str(section.get("id", "unknown")),
            str(section.get("title", "Unknown")),
            flattened,
        )
    return flattened


def _walk_criteria(
    node: dict[str, Any],
    section_id: str,
    section_title: str,
    destination: list[dict[str, Any]],
) -> None:
    for raw in node.get("criteria", []):
        if not isinstance(raw, dict):
            continue
        try:
            weight = float(raw.get("weight", 0))
        except (TypeError, ValueError):
            weight = 0.0
        destination.append(
            {
                "id": str(raw.get("id", "")),
                "section": section_title,
                "section_id": section_id,
                "weight": weight,
                "requirement": str(raw.get("requirement", "")),
            }
        )
    for subsection in node.get("sections", []):
        if isinstance(subsection, dict):
            _walk_criteria(
                subsection,
                str(subsection.get("id", section_id)),
                str(subsection.get("title", section_title)),
                destination,
            )


def _parse_verdict(output: str) -> str | None:
    try:
        payload = json.loads(output.strip())
    except json.JSONDecodeError:
        return None
    verdict = payload.get("verdict") if isinstance(payload, dict) else None
    return verdict if verdict in {"MET", "UNMET"} else None


def _score_run(verdicts: list[tuple[float, str]]) -> tuple[float, float]:
    raw_score = 0.0
    max_possible = 0.0
    favourable = 0
    for weight, verdict in verdicts:
        met = verdict == "MET"
        if weight > 0:
            max_possible += weight
            if met:
                raw_score += weight
                favourable += 1
        elif weight < 0:
            if met:
                raw_score += weight
            else:
                favourable += 1
    normalized = 0.0
    if max_possible:
        normalized = max(0.0, min(1.0, raw_score / max_possible)) * 100
    pass_rate = favourable / len(verdicts) * 100 if verdicts else 0.0
    return normalized, pass_rate


class Plugin(Benchmark):
    extract_criteria = staticmethod(_criteria)

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._judge: JudgePort | None = None

    def bind_judge(self, judge: JudgePort | None) -> None:
        self._judge = judge

    def evaluator_requests_per_case(self, case: Case) -> int:
        if not case.metadata.get("official_evaluator"):
            return 0
        return len(case.metadata.get("criteria", [])) * int(
            self.manifest["official_evaluator"]["judge_runs"]
        )

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
            raise DatasetNotReadyError("DRACO frozen dataset is not materialized and verified")
        rows = [
            json.loads(line)
            for line in snapshot.artifacts[0].path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        sample_n = int(self.manifest["stages"][stage.value]["sample_n"])
        rows = rows[:sample_n]
        cases: list[Case] = []
        for row in rows:
            answer = (
                json.loads(row["answer"]) if isinstance(row.get("answer"), str) else row["answer"]
            )
            criteria = _criteria(answer)
            if not criteria:
                raise DatasetNotReadyError(f"DRACO task has no criteria: {row['id']}")
            cases.append(
                Case(
                    case_id=str(row["id"]),
                    prompt=AGENT_PREFIX + str(row["problem"]),
                    expected=None,
                    metadata={
                        "official_evaluator": True,
                        "problem": str(row["problem"]),
                        "domain": str(row["domain"]),
                        "criteria": criteria,
                    },
                )
            )
        return cases

    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        if not case.metadata.get("official_evaluator"):
            correct = bool(parsed_output and result.citations)
            return CaseScore(
                float(correct),
                correct,
                "citation_fixture_only",
                {"citation_count": len(result.citations)},
            )
        if self._judge is None:
            raise DatasetNotReadyError("DRACO requires a Gateway-backed judge")
        judge_runs = int(self.manifest["official_evaluator"]["judge_runs"])
        temperature = float(self.manifest["official_evaluator"]["judge_temperature"])
        run_scores: list[float] = []
        pass_rates: list[float] = []
        successful_requests = 0
        failed_requests = 0
        total_tokens = 0
        request_ids: list[str] = []
        criteria = list(case.metadata["criteria"])
        for _run in range(judge_runs):
            verdicts: list[tuple[float, str]] = []
            for criterion in criteria:
                try:
                    judged = self._judge.evaluate(
                        JUDGE_USER_TEMPLATE.format(
                            problem=case.metadata.get("problem", case.prompt),
                            response=parsed_output,
                            requirement=criterion["requirement"],
                        ),
                        system_prompt=JUDGE_SYSTEM_PROMPT,
                        temperature=temperature,
                        reasoning_effort=str(
                            self.manifest["official_evaluator"]["judge_reasoning_effort"]
                        ),
                    )
                except GatewayError:
                    failed_requests += 1
                    continue
                verdict = _parse_verdict(judged.output_text)
                if verdict is None:
                    failed_requests += 1
                    continue
                successful_requests += 1
                request_ids.append(judged.request_id)
                total_tokens += int(judged.usage.get("total_tokens", 0))
                verdicts.append((float(criterion["weight"]), verdict))
            if verdicts:
                normalized, pass_rate = _score_run(verdicts)
                run_scores.append(normalized)
                pass_rates.append(pass_rate)
        mean_normalized = statistics.fmean(run_scores) if run_scores else 0.0
        mean_pass_rate = statistics.fmean(pass_rates) if pass_rates else 0.0
        completed_runs = len(run_scores)
        failed_runs = judge_runs - completed_runs
        return CaseScore(
            mean_normalized / 100,
            mean_normalized == 100,
            "draco_rubric_score",
            {
                "domain": case.metadata["domain"],
                "mean_normalized_percent": mean_normalized,
                "mean_pass_rate_percent": mean_pass_rate,
                "judge_runs_completed": completed_runs,
                "judge_runs_failed": failed_runs,
                "evaluator_requests": successful_requests + failed_requests,
                "evaluator_successful_requests": successful_requests,
                "evaluator_failed_requests": failed_requests,
                "evaluator_total_tokens": total_tokens,
                "judge_request_ids": request_ids,
                "criteria_total": len(criteria),
            },
        )

    def aggregate(self, scores: list[CaseScore]) -> dict[str, Any]:
        score = statistics.fmean(item.value for item in scores) if scores else 0.0
        return {
            "metric": "draco_rubric_score",
            "score": score,
            "score_percent": score * 100,
            "tasks": len(scores),
            "evaluator_requests": sum(
                int(item.details.get("evaluator_requests", 0)) for item in scores
            ),
            "evaluator_total_tokens": sum(
                int(item.details.get("evaluator_total_tokens", 0)) for item in scores
            ),
            "tasks_with_judge_failures": sum(
                int(item.details.get("judge_runs_failed", 0)) > 0 for item in scores
            ),
        }
