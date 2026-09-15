"""Build the cost / latency / accuracy comparison report from Inspect AI calibration logs.

Reads the `.eval` logs of one run set, flattens them with `scripts/inspect_summary.py`, folds in
the Platform billing export, and writes `report.json`, `report.csv`, `samples.csv` and a
self-contained `report.html` (inline CSS, hand-rendered SVG, no network at open time).

Router baselines: BestSingle / Oracle / Random (uniform) and the input-agnostic random-mix
line over the fixed models' Pareto front, following the LLMRouterBench / RouterBench / Google
"Universal Model Routing" conventions. The candidate pool is the fixed models only.

Scoring rule: accuracy is `n_correct / n_scored`, where `n_scored` counts only the samples that
came back with a usable score. Samples that ended in a sample-level error are dropped from BOTH
the numerator and the denominator, because every error shape we see is an infrastructure or
upstream fault, not the model getting the question wrong:

  * `RetryError(APIConnectionError)` / `RemoteProtocolError` -- the connection is closed by the
    far end mid-response. Cause is not settled: livecodebench x claude-sonnet-5 piles up on
    730-733s, but the rest spread over 291-767s, and the Gateway does send an SSE heartbeat every
    15s, so this is not a connection sitting idle. Either way the model never delivered an
    answer, and scoring it 0 would measure the transport rather than the model.
  * `RetryError(InternalServerError)` / `Lyra execution failed` -- Lyra emits
    `lyra_execution_failed` inside the SSE stream while the outer HTTP status is still 200, so
    Platform records `ok=true` and bills the request as usual (reproduced 3/3).
  * `RuntimeError('Official LCB grader failed')` -- our own sandboxed grader crashed; the model's
    answer was never judged.

Counting those as wrong systematically penalises whichever variant the infrastructure hurt most:
the main run lost 157 of 2400 samples this way, and they were not spread evenly (gpqa x
lyra-quality alone lost 24 of 60). So the loss is excluded from accuracy and reported on its own
instead -- `n_planned`, `n_error`, `n_missing` and `error_rate` sit next to every accuracy number,
and a group that lost more than `LOSSY_ERROR_RATE` is flagged in the HTML as low-confidence.

Cost attribution rule: Platform bills every Gateway request as its own line item and the Fusion
routing models (`apigo/lyra-*`) appear as their own line items with their own cost, so no
sub-call summing is needed. Inspect does not record the Gateway request id, so a run's cost is
the sum of the export entries whose model equals the variant's model and whose time falls inside
[run started, run completed + tail]. Per-sample cost is an approximation matched by request
duration and is left empty when it cannot be matched one to one.

Cost denominator: `cost_usd_per_sample` divides the run's settled cost by `n_scored`, matching the
accuracy denominator -- it answers "what did one usable answer cost". The numerator deliberately
keeps the money burned on failures (a `lyra_execution_failed` request is billed as usual; a
request whose connection is cut never settles, so it contributes $0 on its own), because that
money was really spent. `cost_usd_per_planned_sample` keeps the old planned-n denominator next to
it so the change of meaning is visible rather than silent.

Usage:
  uv run python scripts/inspect_report.py --logs .local/inspect-logs/calibration \
      --platform .local/platform-logs-today.json --out .local/inspect-report
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import itertools
import json
import math
import statistics
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INSPECT_AI_VERSION = "0.3.263"
MODEL_PREFIX = "openai-api/apigo/"
FUSION_PREFIX = "apigo/lyra-"
DEFAULT_TAIL_SECONDS = 5

# A group that lost more than this share of its planned samples to infrastructure faults gets a
# visual flag in the HTML: the surviving samples may no longer be a fair draw from the frozen set
# (a timeout preferentially eats the long-reasoning questions), so its accuracy is not comparable
# to a group that ran clean.
LOSSY_ERROR_RATE = 0.05

# Sample-level error buckets, matched as substrings against the Inspect error message. Ordered:
# the first bucket whose marker appears wins. All of them are infrastructure/harness faults, so
# the split is only for the report -- none of them changes whether the sample is excluded.
ERROR_KIND_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # The connection was closed mid-response by the far end (cause not settled; see the module
    # docstring). Includes client-side timeouts, which land in the same bucket for the report.
    ("connection", ("APIConnectionError", "APITimeoutError", "ConnectionError", "ReadTimeout")),
    # Upstream returned a failure -- including Lyra's in-stream `lyra_execution_failed`, which
    # arrives under an HTTP 200 and is still billed.
    (
        "upstream",
        (
            "lyra_execution_failed",
            "Lyra execution failed",
            "InternalServerError",
            "APIStatusError",
            "BadRequestError",
            "RateLimitError",
        ),
    ),
    # Our own grader crashed (Docker missing, sandbox timeout, illegal output).
    ("scorer", ("grader failed", "Official LCB grader", "ScorerError")),
)

BENCHMARK_LABELS = {
    "ifeval": "IFEval（指令遵循）",
    "gpqa_diamond": "GPQA Diamond（研究生科学题）",
    "livecodebench_v6": "LiveCodeBench v6（编程题）",
}
BENCHMARK_ORDER = ["ifeval", "gpqa_diamond", "livecodebench_v6"]
VARIANT_ORDER = [
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-6-astra",
    "claude-sonnet-5",
    "claude-opus-5",
    "apigo/lyra-auto",
    "apigo/lyra-budget",
    "apigo/lyra-quality",
]
VARIANT_LABELS = {
    "apigo/lyra-auto": "Fusion auto",
    "apigo/lyra-budget": "Fusion budget",
    "apigo/lyra-quality": "Fusion quality",
}

REPORT_FIELDS = [
    "benchmark",
    "benchmark_label",
    "model",
    "variant_label",
    "effort",
    "max_tokens",
    "harness",
    "is_fusion",
    "n_planned",
    "n_scored",
    "n_correct",
    "n_error",
    "n_missing",
    "error_rate",
    "error_kinds",
    "accuracy",
    "wilson_low",
    "wilson_high",
    "cost_usd_settled",
    "cost_billed_requests",
    "cost_usd_per_sample",
    "cost_usd_per_planned_sample",
    "cost_usd_estimated_public",
    "cost_per_correct_usd",
    "latency_p50_s",
    "latency_p95_s",
    "latency_mean_s",
    "wall_time_s",
    "input_tokens_per_sample",
    "output_tokens_per_sample",
    "reasoning_tokens_per_sample",
    "routing_selected_models",
    "started",
    "completed",
]

SAMPLE_EXTRA_FIELDS = [
    "benchmark",
    "variant",
    "variant_label",
    "is_fusion",
    "cost_usd_matched",
    "cost_match_duration_delta_s",
]


def _load_inspect_summary():
    path = Path(__file__).resolve().parent / "inspect_summary.py"
    spec = importlib.util.spec_from_file_location("inspect_summary", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("inspect_summary", module)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- parsing helpers


def normalize_model(model: str) -> str:
    """`openai-api/apigo/apigo/lyra-auto` -> `apigo/lyra-auto` (the Platform billing model id)."""
    return model[len(MODEL_PREFIX) :] if model.startswith(MODEL_PREFIX) else model


def normalize_benchmark(task: str) -> str:
    return task.rsplit("/", 1)[-1]


def is_fusion(model: str) -> bool:
    return model.startswith(FUSION_PREFIX)


def variant_label(model: str) -> str:
    return VARIANT_LABELS.get(model, model)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


# --------------------------------------------------------------------------- run headers


def dump_header(path: Path) -> dict:
    proc = subprocess.run(
        [
            "uvx",
            "--from",
            f"inspect-ai=={INSPECT_AI_VERSION}",
            "inspect",
            "log",
            "dump",
            "--header-only",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def header_to_run(header: dict, log_name: str) -> dict:
    meta = header.get("eval") or {}
    stats = header.get("stats") or {}
    dataset = meta.get("dataset") or {}
    sample_ids = dataset.get("sample_ids") or []
    started = stats.get("started_at") or meta.get("created") or ""
    completed = stats.get("completed_at") or ""
    wall = ""
    if started and completed:
        wall = round((parse_time(completed) - parse_time(started)).total_seconds(), 3)
    return {
        "log": log_name,
        "task": normalize_benchmark(meta.get("task") or ""),
        "model": normalize_model(meta.get("model") or ""),
        "effort": (meta.get("model_generate_config") or {}).get("reasoning_effort") or "",
        # The output cap the run was launched with. It is not uniform across the matrix any more
        # (scripts/run_calibration.sh gives the Fusion routes a wider cap on GPQA and LCB), so it
        # has to travel with the row instead of being assumed constant by the reader.
        "max_tokens": (meta.get("model_generate_config") or {}).get("max_tokens") or "",
        "started": started,
        "completed": completed,
        "n_planned": len(sample_ids) or dataset.get("samples") or 0,
        "wall_time_s": wall,
        "status": header.get("status", ""),
    }


def collect_runs(logs: list[Path]) -> list[dict]:
    """Extract one run record per `.eval` log (replaces the manual `--header-only` step)."""
    return [header_to_run(dump_header(path), path.name) for path in logs]


# --------------------------------------------------------------------------- platform billing


def load_platform_entries(path: Path | None) -> list[dict]:
    """Accept either the raw `logs/list` payload or the flat list written by the export script."""
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = ((payload.get("data") or {}).get("items")) or payload.get("items") or []
    return [entry for entry in payload if isinstance(entry, dict)]


def entries_for_run(
    run: dict, entries: list[dict], *, tail_seconds: int = DEFAULT_TAIL_SECONDS
) -> list[dict]:
    """Billing line items of this variant's model inside the run's time window."""
    if not run.get("started") or not run.get("completed"):
        return []
    start = parse_time(run["started"])
    end = parse_time(run["completed"]) + timedelta(seconds=tail_seconds)
    matched = []
    for entry in entries:
        if entry.get("model") != run["model"]:
            continue
        stamp = entry.get("time")
        if not stamp:
            continue
        when = parse_time(stamp)
        if start <= when <= end:
            matched.append(entry)
    return matched


def settled_cost(entries: list[dict]) -> tuple[float | None, int]:
    """Sum the settled exact cost; unsettled entries are counted but never valued as zero."""
    total = 0.0
    billed = 0
    for entry in entries:
        if entry.get("settled") is not True:
            continue
        amount = _number(entry.get("cost_usd_exact"))
        if amount is None:
            continue
        total += amount
        billed += 1
    return (total if billed else None), billed


def match_sample_costs(
    rows: list[dict], entries: list[dict], *, tolerance_ratio: float = 0.15, floor_s: float = 2.0
) -> dict[str, tuple[float, float]]:
    """Greedy one-to-one sample<->line-item match by request duration.

    Returns {sample_key: (cost_usd, duration_delta_s)}. Approximate: the Gateway request id is
    not in the Inspect log, so this is the only available per-sample link.
    """
    candidates = []
    for row_index, row in enumerate(rows):
        duration = _number(row.get("total_time_s"))
        if duration is None:
            continue
        for entry_index, entry in enumerate(entries):
            entry_ms = _number(entry.get("duration_ms"))
            cost = _number(entry.get("cost_usd_exact"))
            if entry_ms is None or cost is None or entry.get("settled") is not True:
                continue
            delta = abs(duration - entry_ms / 1000.0)
            if delta <= max(floor_s, duration * tolerance_ratio):
                candidates.append((delta, row_index, entry_index, cost))
    candidates.sort()
    used_rows: set[int] = set()
    used_entries: set[int] = set()
    matched: dict[str, tuple[float, float]] = {}
    for delta, row_index, entry_index, cost in candidates:
        if row_index in used_rows or entry_index in used_entries:
            continue
        used_rows.add(row_index)
        used_entries.add(entry_index)
        matched[str(rows[row_index].get("sample_id"))] = (cost, round(delta, 3))
    return matched


# --------------------------------------------------------------------------- public list prices


def load_public_prices(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = (payload.get("data") or {}).get("items") or []
    prices = {}
    for item in items:
        headline = item.get("pricing_headline") or {}
        slug = item.get("slug")
        if slug and headline:
            prices[slug] = headline
    return prices


def public_estimate(model: str, rows: list[dict], prices: dict[str, dict]) -> float | None:
    """List-price estimate for fixed models; Fusion routing has no public price."""
    if is_fusion(model):
        return None
    headline = prices.get(model)
    if not headline:
        return None
    input_price = _number(headline.get("input_usd_per_1m"))
    output_price = _number(headline.get("output_usd_per_1m"))
    cached_price = _number(headline.get("cached_input_usd_per_1m"))
    if input_price is None or output_price is None:
        return None
    total = 0.0
    seen = False
    for row in rows:
        raw_input = _number(row.get("input_tokens"))
        raw_output = _number(row.get("output_tokens"))
        if raw_input is None and raw_output is None:
            continue
        seen = True
        cached = _number(row.get("cache_read_tokens")) or 0.0
        fresh = max((raw_input or 0.0) - cached, 0.0)
        total += fresh * input_price / 1_000_000
        total += cached * (cached_price if cached_price is not None else input_price) / 1_000_000
        total += (raw_output or 0.0) * output_price / 1_000_000
    return total if seen else None


# --------------------------------------------------------------------------- statistics


def wilson_interval(correct: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """95% Wilson score interval over the samples that actually produced a score.

    `total` must be `n_scored`, never `n_planned`. The interval describes the sampling noise of
    the evidence we have; feeding it the planned n while the numerator only counts scored samples
    would both shift the centre down and narrow the width, i.e. claim more precision than the
    surviving samples support. A group that lost half its samples should read as a wide interval
    over a small n, which is exactly what `n_scored` gives.
    """
    if total <= 0:
        return 0.0, 0.0
    proportion = correct / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    spread = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - spread), min(1.0, center + spread)


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; stable for the n=10 calibration sets."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


# --------------------------------------------------------------------------- aggregation


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _round(value: float | None, digits: int) -> float | str:
    return "" if value is None else round(value, digits)


def build_rows(
    sample_rows: list[dict],
    runs: list[dict],
    entries: list[dict],
    prices: dict[str, dict],
    *,
    tail_seconds: int = DEFAULT_TAIL_SECONDS,
) -> tuple[list[dict], list[dict]]:
    """One aggregate row per (benchmark, variant) plus the per-sample rows with matched cost."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in sample_rows:
        key = (normalize_benchmark(row.get("task") or ""), normalize_model(row.get("model") or ""))
        grouped.setdefault(key, []).append(row)
    runs_by_key = {(run["task"], run["model"]): run for run in runs}

    report: list[dict] = []
    detailed: list[dict] = []
    for key in sorted(set(grouped) | set(runs_by_key), key=_sort_key):
        benchmark, model = key
        rows = grouped.get(key, [])
        run = runs_by_key.get(key, {})
        run_entries = entries_for_run(run, entries, tail_seconds=tail_seconds) if run else []
        cost_total, billed = settled_cost(run_entries)
        matched = match_sample_costs(rows, run_entries)

        n_planned = run.get("n_planned") or len(rows)
        n_error = sum(1 for row in rows if row.get("error"))
        scores = [row.get("score") for row in rows if row.get("score") in {"0", "1"}]
        n_scored = len(scores)
        n_correct = sum(1 for score in scores if score == "1")
        # Samples the log never recorded at all (an aborted run), as opposed to samples that ran
        # and errored. Both are loss, but only the second one has an error message to classify.
        n_missing = max(n_planned - n_scored - n_error, 0)
        # Accuracy over the evidence we actually have. Errored samples carry no score -- the
        # connection was cut or the upstream failed before an answer existed -- so scoring them 0
        # would measure the infrastructure, not the model. They are excluded from numerator and
        # denominator alike and surfaced separately as n_error / error_rate.
        accuracy = n_correct / n_scored if n_scored else None
        low, high = wilson_interval(n_correct, n_scored) if n_scored else (None, None)
        # Loss share of the planned set: everything that failed to yield a score, errors and
        # missing samples together. This is the number that tells a reader how much of the cell
        # is missing, which the accuracy alone no longer shows once the errors are excluded.
        error_rate = (n_planned - n_scored) / n_planned if n_planned else None
        error_kinds = _error_kinds(rows, missing=n_missing)

        latencies = [
            value
            for row in rows
            if not row.get("error") and (value := _number(row.get("total_time_s"))) is not None
        ]
        estimate = public_estimate(model, rows, prices)
        routing = _routing_counts(rows)

        for row in rows:
            cost, delta = matched.get(str(row.get("sample_id")), (None, None))
            detailed.append(
                {
                    **row,
                    "benchmark": benchmark,
                    "variant": model,
                    "variant_label": variant_label(model),
                    "is_fusion": str(is_fusion(model)).lower(),
                    "cost_usd_matched": "" if cost is None else f"{cost:.8f}",
                    "cost_match_duration_delta_s": "" if delta is None else delta,
                }
            )

        report.append(
            {
                "benchmark": benchmark,
                "benchmark_label": BENCHMARK_LABELS.get(benchmark, benchmark),
                "model": model,
                "variant_label": variant_label(model),
                "effort": run.get("effort", ""),
                "max_tokens": run.get("max_tokens", ""),
                "harness": "direct",
                "is_fusion": str(is_fusion(model)).lower(),
                "n_planned": n_planned,
                "n_scored": n_scored,
                "n_correct": n_correct,
                "n_error": n_error,
                "n_missing": n_missing,
                "error_rate": _round(error_rate, 4),
                "error_kinds": json.dumps(error_kinds, ensure_ascii=False, sort_keys=True)
                if error_kinds
                else "",
                "accuracy": _round(accuracy, 4),
                "wilson_low": _round(low, 4),
                "wilson_high": _round(high, 4),
                "cost_usd_settled": "" if cost_total is None else round(cost_total, 6),
                "cost_billed_requests": billed,
                # Settled cost per usable answer: same denominator as accuracy, so the two read
                # together. The numerator keeps the spend on failed attempts (Lyra bills
                # `lyra_execution_failed` as usual; a cut connection never settles and adds $0),
                # because that is what the run really cost.
                "cost_usd_per_sample": ""
                if cost_total is None or not n_scored
                else round(cost_total / n_scored, 6),
                # The previous definition, kept so the switch of denominator is visible in the
                # exports instead of silently changing what the old column name meant.
                "cost_usd_per_planned_sample": ""
                if cost_total is None or not n_planned
                else round(cost_total / n_planned, 6),
                "cost_usd_estimated_public": _round(estimate, 6),
                "cost_per_correct_usd": ""
                if cost_total is None or not n_correct
                else round(cost_total / n_correct, 6),
                "latency_p50_s": _round(percentile(latencies, 0.5), 2),
                "latency_p95_s": _round(percentile(latencies, 0.95), 2),
                "latency_mean_s": _round(_mean(latencies), 2),
                "wall_time_s": run.get("wall_time_s", ""),
                "input_tokens_per_sample": _round(
                    _mean(_column(rows, "input_tokens")),
                    1,
                ),
                "output_tokens_per_sample": _round(_mean(_column(rows, "output_tokens")), 1),
                "reasoning_tokens_per_sample": _round(_mean(_column(rows, "reasoning_tokens")), 1),
                "routing_selected_models": json.dumps(routing, ensure_ascii=False, sort_keys=True)
                if routing
                else "",
                "started": run.get("started", ""),
                "completed": run.get("completed", ""),
            }
        )
    return report, detailed


def _column(rows: list[dict], field: str) -> list[float]:
    return [value for row in rows if (value := _number(row.get(field))) is not None]


def classify_error(message: str) -> str:
    """Bucket one Inspect sample error into connection / upstream / scorer / other.

    Only used to describe the loss in the report -- the exclusion from accuracy does not depend on
    the bucket, because none of these shapes is the model answering wrongly. The raw messages are
    not groupable as-is: Inspect wraps them as `RetryError(<Future at 0x... raised X>)`, so the
    memory address makes every message unique and only the inner exception name is stable.
    """
    for kind, markers in ERROR_KIND_MARKERS:
        if any(marker in message for marker in markers):
            return kind
    return "other"


def _error_kinds(rows: list[dict], *, missing: int = 0) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        message = row.get("error") or ""
        if message:
            kind = classify_error(message)
            counts[kind] = counts.get(kind, 0) + 1
    if missing:
        counts["missing"] = missing
    return counts


def _routing_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        selected = row.get("lyra_selected_model") or ""
        if selected:
            counts[selected] = counts.get(selected, 0) + 1
    return counts


def _sort_key(key: tuple[str, str]) -> tuple[int, str, int, str]:
    benchmark, model = key
    benchmark_rank = (
        BENCHMARK_ORDER.index(benchmark) if benchmark in BENCHMARK_ORDER else len(BENCHMARK_ORDER)
    )
    model_rank = VARIANT_ORDER.index(model) if model in VARIANT_ORDER else len(VARIANT_ORDER)
    return benchmark_rank, benchmark, model_rank, model


# --------------------------------------------------------------------------- router baselines

# Standard router-evaluation baselines (LLMRouterBench / RouterBench / Google "Universal Model
# Routing"). The candidate pool is the FIXED models only -- they are the alternatives the router
# chooses between, so a Fusion variant is never its own baseline. Everything is computed per
# benchmark on the same frozen sample ids; a sample counts as correct for a model only when
# score == "1".
#
# The denominator follows the same rule as the per-variant accuracy: a sample that no pool model
# managed to score is infrastructure loss, not a question everyone got wrong, so it leaves the
# oracle denominator entirely. A sample that at least one model scored stays in -- there the pool
# genuinely had a shot at it, and a model that errored on it simply is not a candidate for that
# sample. Keeping such samples in on the old n_planned basis would drag the Oracle below
# BestSingle whenever a fixed model lost samples, which is arithmetically impossible for an upper
# bound and a sure sign the denominators had drifted apart.

BASELINE_CSV_MODELS = {
    "best_single": "baseline:best_single",
    "oracle": "baseline:oracle",
    "random": "baseline:random",
}
BASELINE_LABELS = {
    "best_single": "BestSingle（最强单模型）",
    "oracle": "Oracle（理论上界）",
    "random": "Random（均匀随机）",
}
COST_FALLBACK_NOTE = (
    "逐题成本优先用按请求时长匹配到的账单金额；匹配不上的题回退到该模型在本赛道的"
    "运行平均每题成本（总结算成本 ÷ 成功评分题数）。"
)


def _sample_cost_table(
    detailed: list[dict], benchmark: str, model: str, run_average: float | None
) -> tuple[dict[str, float], int]:
    """{sample_id: cost} over the model's SCORED samples: matched cost, else the run average.

    Errored samples are skipped. They have no usable answer, so they cannot be a routing target
    for the Oracle, and letting them take the run-average fallback would price a failure as if it
    had produced an answer. The money those attempts did cost is not lost from the report -- it
    stays inside `cost_usd_settled`, and therefore inside the run average used as the fallback.
    """
    costs: dict[str, float] = {}
    fallback = 0
    for row in detailed:
        if row.get("benchmark") != benchmark or row.get("variant") != model:
            continue
        if row.get("score") not in {"0", "1"}:
            continue
        sample_id = str(row.get("sample_id"))
        matched = _number(row.get("cost_usd_matched"))
        if matched is not None:
            costs[sample_id] = matched
        elif run_average is not None:
            costs[sample_id] = run_average
            fallback += 1
    return costs, fallback


def pareto_front(points: list[dict]) -> list[dict]:
    """Empirical Pareto front in (cost, accuracy): cheapest first, keep strict quality gains.

    Points are `{"model", "cost_usd_per_sample", "accuracy"}`; a model that is both pricier and
    less accurate than another is dominated and drops out.
    """
    ordered = sorted(points, key=lambda point: (point["cost_usd_per_sample"], -point["accuracy"]))
    front: list[dict] = []
    best: float | None = None
    for point in ordered:
        if best is None or point["accuracy"] > best + 1e-12:
            front.append(point)
            best = point["accuracy"]
    return front


def random_mix_accuracy(front: list[dict], cost: float | None) -> float | None:
    """Accuracy an input-agnostic random mix of the two adjacent front models reaches at `cost`.

    Mixing two front points with probability p traces the straight segment between them, so the
    frontier of all input-agnostic routers is the polyline through the front (Google UMR baseline).
    Outside the front's cost range the polyline is clamped: no mix is cheaper than the cheapest
    model or more accurate than the best one.
    """
    if not front or cost is None:
        return None
    if cost <= front[0]["cost_usd_per_sample"]:
        return front[0]["accuracy"]
    if cost >= front[-1]["cost_usd_per_sample"]:
        return front[-1]["accuracy"]
    for left, right in itertools.pairwise(front):
        low, high = left["cost_usd_per_sample"], right["cost_usd_per_sample"]
        if low <= cost <= high:
            span = high - low
            if span <= 0:
                return max(left["accuracy"], right["accuracy"])
            return left["accuracy"] + (cost - low) / span * (right["accuracy"] - left["accuracy"])
    return front[-1]["accuracy"]


def random_mix_cost(front: list[dict], accuracy: float | None) -> float | None:
    """Cheapest cost at which an input-agnostic random mix reaches `accuracy`.

    The inverse of `random_mix_accuracy`, so a point can be judged on the cost axis too: equal
    accuracy for less money is as much a win over input-agnostic mixing as more accuracy for the
    same money. `None` means the mix cannot reach that accuracy at any price.
    """
    if not front or accuracy is None:
        return None
    if accuracy <= front[0]["accuracy"]:
        return front[0]["cost_usd_per_sample"]
    if accuracy > front[-1]["accuracy"]:
        return None
    for left, right in itertools.pairwise(front):
        if left["accuracy"] <= accuracy <= right["accuracy"]:
            span = right["accuracy"] - left["accuracy"]
            if span <= 0:
                return min(left["cost_usd_per_sample"], right["cost_usd_per_sample"])
            ratio = (accuracy - left["accuracy"]) / span
            return left["cost_usd_per_sample"] + ratio * (
                right["cost_usd_per_sample"] - left["cost_usd_per_sample"]
            )
    return front[-1]["cost_usd_per_sample"]


def classify_vs_mix(
    front: list[dict], cost: float | None, accuracy: float | None, *, eps: float = 1e-9
) -> str:
    """是 / 否 / 持平 against the input-agnostic random-mix line.

    A point counts as above the line when it is higher (more accurate at the same cost) or to its
    left (as accurate for less money); 否 is the mirror image, and everything else sits on it.
    """
    if accuracy is None or cost is None or not front:
        return "—"
    line_accuracy = random_mix_accuracy(front, cost)
    line_cost = random_mix_cost(front, accuracy)
    above = line_accuracy is not None and accuracy > line_accuracy + eps
    below = line_accuracy is not None and accuracy < line_accuracy - eps
    left = line_cost is None or cost < line_cost - eps
    right = line_cost is not None and cost > line_cost + eps
    if above or left:
        return "是"
    if below or right:
        return "否"
    return "持平"


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _baseline_entry(label: str, accuracy: float | None, cost: float | None) -> dict:
    return {"label": label, "accuracy": _r(accuracy, 4), "cost_usd_per_sample": _r(cost, 6)}


def compute_baselines(report: list[dict], detailed: list[dict]) -> dict[str, dict]:
    """BestSingle / Oracle / Random / Pareto-random-mix per benchmark, over the fixed-model pool."""
    by_benchmark: dict[str, list[dict]] = {}
    for row in report:
        by_benchmark.setdefault(row["benchmark"], []).append(row)

    result: dict[str, dict] = {}
    for benchmark, rows in by_benchmark.items():
        fixed_rows = [row for row in rows if row["is_fusion"] != "true"]
        if not fixed_rows:
            continue
        n_planned = max((int(row["n_planned"] or 0) for row in fixed_rows), default=0)

        pool: list[dict] = []
        fallback_by_model: dict[str, int] = {}
        for row in fixed_rows:
            model = row["model"]
            run_average = _number(row.get("cost_usd_per_sample"))
            costs, fallback = _sample_cost_table(detailed, benchmark, model, run_average)
            fallback_by_model[model] = fallback
            correct = {
                str(sample["sample_id"])
                for sample in detailed
                if sample.get("benchmark") == benchmark
                and sample.get("variant") == model
                and sample.get("score") == "1"
            }
            pool.append(
                {
                    "model": model,
                    "accuracy": _number(row.get("accuracy")),
                    "cost": _mean(list(costs.values())),
                    "costs": costs,
                    "correct": correct,
                }
            )

        # The evaluable set: samples at least one pool model actually scored. A sample every model
        # lost to a connection cut carries no evidence about routing, so it is dropped here the
        # same way errored samples are dropped from each variant's accuracy.
        pool_models = {entry["model"] for entry in pool}
        sample_ids = sorted(
            {
                str(sample["sample_id"])
                for sample in detailed
                if sample.get("benchmark") == benchmark
                and sample.get("variant") in pool_models
                and sample.get("score") in {"0", "1"}
            }
        )
        denominator = len(sample_ids)

        best = sorted(
            pool,
            key=lambda entry: (
                -(entry["accuracy"] if entry["accuracy"] is not None else -1.0),
                entry["cost"] if entry["cost"] is not None else math.inf,
                VARIANT_ORDER.index(entry["model"])
                if entry["model"] in VARIANT_ORDER
                else len(VARIANT_ORDER),
            ),
        )[0]

        chosen_costs: list[float] = []
        solved = 0
        routable = 0
        for sample_id in sample_ids:
            winners = [entry for entry in pool if sample_id in entry["correct"]]
            priced = [entry for entry in pool if sample_id in entry["costs"]]
            cheapest = min(priced, key=lambda entry: entry["costs"][sample_id]) if priced else None
            if winners:
                solved += 1
                priced_winners = [entry for entry in winners if sample_id in entry["costs"]]
                if priced_winners:
                    chosen_costs.append(min(e["costs"][sample_id] for e in priced_winners))
                if cheapest is not None and sample_id not in cheapest["correct"]:
                    routable += 1
            elif cheapest is not None:
                # Nobody solved it, so the router cannot do better than the cheapest attempt.
                chosen_costs.append(cheapest["costs"][sample_id])

        complete = bool(sample_ids) and len(chosen_costs) == len(sample_ids)
        oracle_cost = _mean(chosen_costs) if complete else None
        oracle_accuracy = solved / denominator if denominator else None

        accuracies = [e["accuracy"] for e in pool if e["accuracy"] is not None]
        pool_costs = [e["cost"] for e in pool if e["cost"] is not None]
        random_accuracy = _mean(accuracies)
        random_cost = _mean(pool_costs) if len(pool_costs) == len(pool) else None

        front = pareto_front(
            [
                {
                    "model": entry["model"],
                    "cost_usd_per_sample": _r(entry["cost"], 6),
                    "accuracy": _r(entry["accuracy"], 4),
                }
                for entry in pool
                if entry["cost"] is not None and entry["accuracy"] is not None
            ]
        )

        best_accuracy = best["accuracy"]
        headroom_span = (
            None
            if oracle_accuracy is None or best_accuracy is None
            else oracle_accuracy - best_accuracy
        )

        variants = []
        for row in rows:
            if row["is_fusion"] != "true":
                continue
            accuracy = _number(row.get("accuracy"))
            cost = _number(row.get("cost_usd_per_sample"))
            unusable = (
                headroom_span is None
                or accuracy is None
                or best_accuracy is None
                or abs(headroom_span) < 1e-12
            )
            headroom = None if unusable else (accuracy - best_accuracy) / headroom_span
            variants.append(
                {
                    "model": row["model"],
                    "label": variant_label(row["model"]),
                    "accuracy": _r(accuracy, 4),
                    "cost_usd_per_sample": _r(cost, 6),
                    "cost_vs_best_single": _r(
                        None if not cost or not best["cost"] else cost / best["cost"], 4
                    ),
                    "headroom_captured": _r(headroom, 4),
                    "headroom_is_empty": headroom_span is not None and abs(headroom_span) < 1e-12,
                    "gap_to_oracle_pp": _r(
                        None
                        if accuracy is None or oracle_accuracy is None
                        else (oracle_accuracy - accuracy) * 100,
                        2,
                    ),
                    "above_random_mix": classify_vs_mix(front, cost, accuracy),
                }
            )

        result[benchmark] = {
            "benchmark_label": BENCHMARK_LABELS.get(benchmark, benchmark),
            "pool": [entry["model"] for entry in pool],
            "n_planned": n_planned,
            # The denominator the Oracle / Random shares below are computed on: planned samples
            # minus the ones no pool model ever scored.
            "n_evaluable": denominator,
            "best_single": {
                "model": best["model"],
                **_baseline_entry(variant_label(best["model"]), best["accuracy"], best["cost"]),
            },
            "oracle": {
                **_baseline_entry(BASELINE_LABELS["oracle"], oracle_accuracy, oracle_cost),
                "routable_share": _r(routable / denominator if denominator else None, 4),
                "unsolved_share": _r(
                    (denominator - solved) / denominator if denominator else None, 4
                ),
            },
            "random_uniform": _baseline_entry(
                BASELINE_LABELS["random"], random_accuracy, random_cost
            ),
            "pareto_front": front,
            "variants": variants,
            "cost_fallback_samples": {
                "total": sum(fallback_by_model.values()),
                "by_model": fallback_by_model,
            },
            "cost_fallback_note": COST_FALLBACK_NOTE,
        }
    return result


def baseline_csv_rows(baselines: dict[str, dict]) -> list[dict]:
    """`baseline:*` rows for report.csv: accuracy and cost/sample filled, everything else empty."""
    rows: list[dict] = []
    for benchmark in sorted(baselines, key=lambda name: _sort_key((name, ""))):
        block = baselines[benchmark]
        for key in ("best_single", "oracle", "random"):
            source = block["random_uniform"] if key == "random" else block[key]
            accuracy = source.get("accuracy")
            cost = source.get("cost_usd_per_sample")
            row = dict.fromkeys(REPORT_FIELDS, "")
            row.update(
                {
                    "benchmark": benchmark,
                    "benchmark_label": block["benchmark_label"],
                    "model": BASELINE_CSV_MODELS[key],
                    "variant_label": BASELINE_LABELS[key],
                    "accuracy": "" if accuracy is None else accuracy,
                    "cost_usd_per_sample": "" if cost is None else cost,
                }
            )
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- HTML rendering

PALETTE = {
    # dataviz skill reference palette: slot 1 (blue) = Fusion routing, slot 2 (orange) = fixed
    # models. Validated all-pairs in both modes; marker fill and direct labels are the
    # secondary encoding so identity never rests on color alone.
    "light": {
        "page": "#f9f9f7",
        "surface": "#fcfcfb",
        "primary": "#0b0b0b",
        "secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "fusion": "#2a78d6",
        "fixed": "#eb6834",
        "border": "rgba(11,11,11,0.10)",
        # Loss flag: a warm amber that stays legible on the light surface and does not collide
        # with either series color (the fixed-model orange is reserved for data marks).
        "warn": "#9a5b00",
        "warn_bg": "rgba(154,91,0,0.08)",
    },
    "dark": {
        "page": "#0d0d0d",
        "surface": "#1a1a19",
        "primary": "#ffffff",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "fusion": "#3987e5",
        "fixed": "#d95926",
        "border": "rgba(255,255,255,0.10)",
        "warn": "#e0a44a",
        "warn_bg": "rgba(224,164,74,0.12)",
    },
}

CSS = """
:root { color-scheme: light dark; }
.viz-root {
  --page: %(l_page)s; --surface: %(l_surface)s; --text-primary: %(l_primary)s;
  --text-secondary: %(l_secondary)s; --muted: %(l_muted)s; --grid: %(l_grid)s;
  --axis: %(l_axis)s; --fusion: %(l_fusion)s; --fixed: %(l_fixed)s; --border: %(l_border)s;
  --warn: %(l_warn)s; --warn-bg: %(l_warn_bg)s;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    --page: %(d_page)s; --surface: %(d_surface)s; --text-primary: %(d_primary)s;
    --text-secondary: %(d_secondary)s; --muted: %(d_muted)s; --grid: %(d_grid)s;
    --axis: %(d_axis)s; --fusion: %(d_fusion)s; --fixed: %(d_fixed)s; --border: %(d_border)s;
    --warn: %(d_warn)s; --warn-bg: %(d_warn_bg)s;
  }
}
body { margin: 0; background: var(--page); }
.viz-root {
  font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: var(--text-primary); background: var(--page); padding: 32px 28px 64px;
  max-width: 1080px; margin: 0 auto; line-height: 1.6;
}
h1 { font-size: 26px; margin: 0 0 6px; }
h2 { font-size: 20px; margin: 40px 0 4px; }
h3 { font-size: 16px; margin: 22px 0 4px; font-weight: 600; }
p.sub { color: var(--text-secondary); margin: 4px 0; font-size: 14px; }
p.note { color: var(--muted); font-size: 13px; margin: 6px 0 0; }
section.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 20px 22px; margin-top: 16px;
}
table { border-collapse: collapse; width: 100%%; font-size: 13px; margin-top: 10px; }
th, td { text-align: right; padding: 7px 8px; border-bottom: 1px solid var(--grid); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--text-secondary); font-weight: 600; }
td { font-variant-numeric: tabular-nums; }
tr.fusion td:first-child { font-weight: 600; }
/* Loss flag: a tinted row plus a text badge, so the warning survives greyscale printing and
   never rests on color alone. */
tr.lossy { background: var(--warn-bg); }
td.lossy-cell { color: var(--warn); font-weight: 600; }
/* Output cap that differs from the smallest one in the same benchmark. Underlined as well as
   coloured so the "this cell is not on the same footing" signal survives greyscale printing. */
td.wider-cap { color: var(--warn); font-weight: 600; text-decoration: underline dotted; }
.badge {
  display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 999px;
  font-size: 11px; font-weight: 600; color: var(--warn); border: 1px solid var(--warn);
  background: var(--surface); vertical-align: 1px;
}
.swatch {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%%; margin-right: 6px;
}
.swatch.fusion { background: var(--fusion); }
.swatch.fixed { background: transparent; border: 2px solid var(--fixed); }
.legend {
  display: flex; gap: 20px; font-size: 13px; color: var(--text-secondary); margin: 4px 0 8px;
}
.links a {
  display: inline-block; margin-right: 12px; padding: 8px 14px; border-radius: 8px;
  border: 1px solid var(--border); color: var(--text-primary); text-decoration: none;
  background: var(--surface); font-size: 14px;
}
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
.kv { font-size: 13px; color: var(--text-secondary); margin: 2px 0; }
.kv b { color: var(--text-primary); font-variant-numeric: tabular-nums; }
svg { display: block; max-width: 100%%; height: auto; }
"""


def _css() -> str:
    values = {f"l_{k}": v for k, v in PALETTE["light"].items()}
    values.update({f"d_{k}": v for k, v in PALETTE["dark"].items()})
    return CSS % values


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def fmt(value: Any, digits: int = 2, suffix: str = "", dash: str = "—") -> str:
    number = _number(value)
    if number is None:
        return dash
    return f"{number:,.{digits}f}{suffix}"


def fmt_money(value: Any, digits: int = 4) -> str:
    number = _number(value)
    return "—" if number is None else f"${number:,.{digits}f}"


def fmt_pct(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number * 100:.0f}%"


def _money_tick_label(value: float) -> str:
    """Axis tick label with ~3 significant digits; exact zero renders as "$0", not "$0.0000"."""
    if value is None or value < 1e-9:
        return "$0"
    magnitude = math.floor(math.log10(abs(value)))
    decimals = max(0, min(6, 2 - magnitude))
    return f"${value:.{decimals}f}"


class Axis:
    """Linear or log₁₀ mapping from data values to pixels, with human tick labels."""

    def __init__(self, low: float, high: float, pixel_low: float, pixel_high: float, log: bool):
        self.log = log and low > 0
        self.low = math.log10(low) if self.log else low
        self.high = math.log10(high) if self.log else high
        if self.high - self.low < 1e-12:
            self.high = self.low + 1.0
        self.pixel_low = pixel_low
        self.pixel_high = pixel_high

    def to_pixel(self, value: float) -> float:
        raw = math.log10(value) if self.log else value
        ratio = (raw - self.low) / (self.high - self.low)
        return self.pixel_low + ratio * (self.pixel_high - self.pixel_low)

    def ticks(self, count: int = 5) -> list[float]:
        steps = [self.low + (self.high - self.low) * i / (count - 1) for i in range(count)]
        return [10**step if self.log else step for step in steps]


def _svg_text(
    x: float,
    y: float,
    text: str,
    *,
    fill: str,
    size: int = 12,
    anchor: str = "start",
    weight: str = "normal",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{esc(text)}</text>'
    )


def _text_width(text: str, size: int = 12) -> float:
    """Rough glyph-width estimate in a system-ui sans-serif at `size`.

    CJK glyphs and full-width punctuation are roughly square, so they count as a full em; Latin
    averages about 0.58 em. The baseline labels mix both, and treating them as all Latin would
    under-measure the box and let the collision pass overlap them.
    """
    return sum(size * (1.0 if ord(char) > 0x2E80 else 0.58) for char in text)


def _boxes_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    ax0, ax1, ay0, ay1 = a
    bx0, bx1, by0, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def _place_labels(
    points: list[dict], *, left: float, right: float, top: float, bottom: float, size: int = 12
) -> list[dict]:
    """Greedy label placement: alternate above/below in fixed steps until boxes stop colliding.

    Points are placed in x order so nearby labels resolve deterministically left to right. Each
    result carries `label_x`/`label_y` (clamped to the plot area) and `leader` (True when the
    label moved far enough from its natural position that a leader line back to the point helps).
    """
    step = 14
    placed_boxes: list[tuple[float, float, float, float]] = []
    by_index = sorted(range(len(points)), key=lambda i: points[i]["x"])
    results: list[dict | None] = [None] * len(points)
    for i in by_index:
        point = points[i]
        text = point["label"]
        width = _text_width(text, size)
        natural_x = point["x"] + 12
        natural_y = point["y"] + 4
        base_x = min(natural_x, right - width)
        base_x = max(base_x, left)

        offsets = [4]
        for k in range(1, 9):
            offsets.append(4 - step * k)
            offsets.append(4 + step * k)

        chosen_y = None
        for offset in offsets:
            candidate_y = point["y"] + offset
            candidate_y = min(max(candidate_y, top + size), bottom)
            box = (base_x, base_x + width, candidate_y - size, candidate_y)
            if not any(_boxes_overlap(box, other) for other in placed_boxes):
                chosen_y = candidate_y
                break
        if chosen_y is None:
            chosen_y = min(max(point["y"] + offsets[-1], top + size), bottom)

        placed_boxes.append((base_x, base_x + width, chosen_y - size, chosen_y))
        leader = abs(chosen_y - natural_y) > 6 or abs(base_x - natural_x) > 6
        results[i] = {**point, "label_x": base_x, "label_y": chosen_y, "leader": leader}
    return results  # type: ignore[return-value]


def _star_path(cx: float, cy: float, outer: float = 9.0, inner: float = 4.0) -> str:
    """Five-pointed star centred on (cx, cy); shape carries the Oracle identity, not color."""
    coords = []
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = -math.pi / 2 + index * math.pi / 5
        coords.append(f"{cx + radius * math.cos(angle):.1f},{cy + radius * math.sin(angle):.1f}")
    return "M" + "L".join(coords) + "Z"


LEGEND_GLYPHS = {
    "oracle": '<svg width="14" height="14" viewBox="-8 -8 16 16" style="vertical-align:-2px">'
    f'<path d="{_star_path(0, 0, 7.0, 3.1)}" fill="var(--text-secondary)"/></svg>',
    "random": '<svg width="12" height="12" viewBox="-6 -6 12 12" style="vertical-align:-1px">'
    '<rect x="-4" y="-4" width="8" height="8" rx="1.5" fill="var(--muted)"/></svg>',
    "mix": '<svg width="22" height="10" viewBox="0 0 22 10" style="vertical-align:-1px">'
    '<line x1="1" y1="5" x2="21" y2="5" stroke="var(--muted)" stroke-width="2" '
    'stroke-dasharray="5 4" stroke-linecap="round"/></svg>',
    "best": '<svg width="16" height="16" viewBox="-9 -9 18 18" style="vertical-align:-3px">'
    '<circle r="7.5" fill="none" stroke="var(--text-secondary)" stroke-width="1.5"/></svg>',
    # Loss flag: same dashed halo the scatter draws around a point whose group lost too many
    # samples, so the legend entry and the mark are literally the same shape.
    "lossy": '<svg width="18" height="18" viewBox="-10 -10 20 20" style="vertical-align:-4px">'
    '<circle r="8" fill="none" stroke="var(--warn)" stroke-width="1.5" '
    'stroke-dasharray="3 3"/></svg>',
}


def scatter_svg(rows: list[dict], baseline: dict | None = None) -> str:
    """Quality vs cost: x = settled cost per sample, y = accuracy with Wilson bars.

    With `baseline` the plot also carries the router-evaluation reference marks: a ring around
    the BestSingle point, an Oracle star, a Random-uniform square, and the dashed
    input-agnostic random-mix line through the fixed models' Pareto front.
    """
    points = [
        row
        for row in rows
        if _number(row.get("cost_usd_per_sample")) is not None
        and _number(row.get("accuracy")) is not None
    ]
    if not points:
        return '<p class="note">该赛道没有可用的结算成本，散点图省略。</p>'
    width, height = 760, 380
    # top has extra headroom for the y-axis title so it never sits under the "100%" tick label.
    left, right, top, bottom = 70, 190, 40, 56
    overlay_costs = []
    if baseline:
        for block in (baseline["oracle"], baseline["random_uniform"]):
            value = _number(block.get("cost_usd_per_sample"))
            if value is not None:
                overlay_costs.append(value)
    costs = [_number(row["cost_usd_per_sample"]) for row in points] + overlay_costs
    low, high = min(costs), max(costs)
    use_log = low > 0 and high / low > 20
    pad = 1.35 if use_log else 1.0
    x_axis = Axis(
        low / pad if use_log else 0.0,
        high * pad if use_log else high * 1.12,
        left,
        width - right,
        use_log,
    )
    # y range runs to 105% so points at 100% and their Wilson caps clear the top edge.
    y_axis = Axis(0.0, 1.05, height - bottom, top, False)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="准确率与每题成本的对比散点图">'
    ]
    for value in [0.0, 0.25, 0.5, 0.75, 1.0]:
        y = y_axis.to_pixel(value)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(_svg_text(left - 10, y + 4, fmt_pct(value), fill="var(--muted)", anchor="end"))
    for tick in x_axis.ticks(5):
        x = x_axis.to_pixel(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height - bottom}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(
            _svg_text(
                x,
                height - bottom + 18,
                _money_tick_label(tick),
                fill="var(--muted)",
                anchor="middle",
            )
        )
    parts.append(
        f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" '
        f'stroke="var(--axis)" stroke-width="1"/>'
    )
    parts.append(
        _svg_text(
            (left + width - right) / 2,
            height - 12,
            "每题成本（美元，" + ("对数刻度" if use_log else "线性刻度") + "，越左越便宜）",
            fill="var(--text-secondary)",
            anchor="middle",
        )
    )
    # Y-axis title sits above the plot, outside the tick-label column, so it never collides
    # with the "100%" tick text.
    parts.append(_svg_text(left, 16, "准确率", fill="var(--text-secondary)", anchor="start"))

    # Input-agnostic random-mix line: the polyline through the fixed models' Pareto front is
    # exactly what a router that ignores the question can reach by mixing two of them.
    front = (baseline or {}).get("pareto_front") or []
    if len(front) >= 2:
        path = " ".join(
            f"{x_axis.to_pixel(_number(p['cost_usd_per_sample'])):.1f},"
            f"{y_axis.to_pixel(_number(p['accuracy'])):.1f}"
            for p in front
        )
        parts.append("<g><title>随机混合线（输入无关路由的上界）</title>")
        parts.append(
            f'<polyline points="{path}" fill="none" stroke="var(--muted)" stroke-width="2" '
            f'stroke-dasharray="6 5" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        parts.append("</g>")

    point_meta = []
    best_model = ((baseline or {}).get("best_single") or {}).get("model")
    for row in points:
        fusion = row["is_fusion"] == "true"
        color = "var(--fusion)" if fusion else "var(--fixed)"
        x = x_axis.to_pixel(_number(row["cost_usd_per_sample"]))
        y = y_axis.to_pixel(_number(row["accuracy"]))
        y_low = y_axis.to_pixel(_number(row["wilson_low"]) or 0.0)
        y_high = y_axis.to_pixel(_number(row["wilson_high"]) or 0.0)
        lossy = is_lossy(row)
        tip = (
            f"{variant_label(row['model'])}｜准确率 {fmt_pct(row['accuracy'])}"
            f"（{row['n_correct']}/{row['n_scored']}）"
            f"｜每题 {fmt_money(row['cost_usd_per_sample'])}"
            f"｜p50 {fmt(row['latency_p50_s'], 1, ' 秒')}"
            f"｜损耗 {fmt_pct(row['error_rate'])}（{row['n_planned']} 题计划）"
        )
        parts.append(f"<g><title>{esc(tip)}</title>")
        parts.append(
            f'<line x1="{x:.1f}" y1="{y_low:.1f}" x2="{x:.1f}" y2="{y_high:.1f}" '
            f'stroke="{color}" stroke-width="2" opacity="0.55"/>'
        )
        if fusion:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{color}" '
                f'stroke="var(--surface)" stroke-width="2"/>'
            )
        else:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="var(--surface)" '
                f'stroke="{color}" stroke-width="2.5"/>'
            )
        label = variant_label(row["model"])
        if best_model and row["model"] == best_model:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="none" '
                f'stroke="var(--text-secondary)" stroke-width="1.5"/>'
            )
            label = f"{label}（BestSingle）"
        if lossy:
            # Dashed halo + a loss share in the label: this point is computed on far fewer
            # samples than planned, so it should not be read as comparable to the clean ones.
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="13" fill="none" stroke="var(--warn)" '
                f'stroke-width="1.5" stroke-dasharray="3 3"/>'
            )
            label = f"{label}（损耗 {fmt_pct(row['error_rate'])}）"
        parts.append("</g>")
        point_meta.append({"x": x, "y": y, "label": label})

    # Oracle and Random-uniform ride the same label-collision pass as the model points, so the
    # new marks never overprint an existing label.
    overlays = [
        ("oracle", baseline.get("oracle") if baseline else None, "Oracle（理论上界）"),
        ("random", baseline.get("random_uniform") if baseline else None, "随机（均匀）"),
    ]
    for kind, block, text in overlays:
        if not block:
            continue
        cost = _number(block.get("cost_usd_per_sample"))
        accuracy = _number(block.get("accuracy"))
        if cost is None or accuracy is None:
            continue
        ox, oy = x_axis.to_pixel(cost), y_axis.to_pixel(accuracy)
        tip = f"{text}｜准确率 {fmt_pct(accuracy)}｜每题 {fmt_money(cost)}"
        parts.append(f"<g><title>{esc(tip)}</title>")
        if kind == "oracle":
            parts.append(
                f'<path d="{_star_path(ox, oy, 9.5, 4.2)}" fill="var(--text-secondary)" '
                f'stroke="var(--surface)" stroke-width="2"/>'
            )
        else:
            parts.append(
                f'<rect x="{ox - 4.5:.1f}" y="{oy - 4.5:.1f}" width="9" height="9" rx="1.5" '
                f'fill="var(--muted)" stroke="var(--surface)" stroke-width="2"/>'
            )
        parts.append("</g>")
        point_meta.append({"x": ox, "y": oy, "label": text})

    # Label placement pass: sort by x, alternate above/below in fixed steps on collision, and
    # draw a thin leader line whenever a label had to move away from its natural spot.
    labels = _place_labels(
        point_meta, left=left, right=width - right, top=top, bottom=height - bottom
    )
    for label in labels:
        if label["leader"]:
            parts.append(
                f'<line x1="{label["x"]:.1f}" y1="{label["y"]:.1f}" '
                f'x2="{label["label_x"] - 2:.1f}" y2="{label["label_y"] - 4:.1f}" '
                f'stroke="var(--muted)" stroke-width="1"/>'
            )
        parts.append(
            _svg_text(
                label["label_x"], label["label_y"], label["label"], fill="var(--text-primary)"
            )
        )
    parts.append("</svg>")
    return "".join(parts)


def latency_svg(rows: list[dict]) -> str:
    """p50 → p95 dot-range per variant, one row per variant, seconds on a linear axis."""
    usable = [row for row in rows if _number(row.get("latency_p50_s")) is not None]
    if not usable:
        return '<p class="note">没有可用的耗时数据。</p>'
    row_height = 26
    width = 760
    left, right, top = 150, 60, 18
    height = top + row_height * len(usable) + 44
    top_value = max(_number(row.get("latency_p95_s")) or 0.0 for row in usable) * 1.12 or 1.0
    axis = Axis(0.0, top_value, left, width - right, False)
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="每题耗时的中位数与 p95">']
    for tick in axis.ticks(5):
        x = axis.to_pixel(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height - 40:.1f}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(_svg_text(x, height - 22, f"{tick:.0f}", fill="var(--muted)", anchor="middle"))
    parts.append(
        _svg_text(
            (left + width - right) / 2,
            height - 6,
            "每题耗时（秒，越左越快）",
            fill="var(--text-secondary)",
            anchor="middle",
        )
    )
    for index, row in enumerate(usable):
        y = top + row_height * index + row_height / 2
        fusion = row["is_fusion"] == "true"
        color = "var(--fusion)" if fusion else "var(--fixed)"
        p50 = _number(row["latency_p50_s"])
        p95 = _number(row["latency_p95_s"]) or p50
        x50, x95 = axis.to_pixel(p50), axis.to_pixel(p95)
        tip = f"{variant_label(row['model'])}｜中位 {p50:.1f} 秒｜p95 {p95:.1f} 秒"
        parts.append(
            _svg_text(
                left - 12,
                y + 4,
                variant_label(row["model"]),
                fill="var(--text-primary)",
                anchor="end",
            )
        )
        parts.append(f"<g><title>{esc(tip)}</title>")
        parts.append(
            f'<line x1="{x50:.1f}" y1="{y:.1f}" x2="{x95:.1f}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="2" opacity="0.5"/>'
        )
        parts.append(
            f'<circle cx="{x95:.1f}" cy="{y:.1f}" r="5" fill="var(--surface)" '
            f'stroke="{color}" stroke-width="2.5"/>'
        )
        parts.append(
            f'<circle cx="{x50:.1f}" cy="{y:.1f}" r="6" fill="{color}" '
            f'stroke="var(--surface)" stroke-width="2"/>'
        )
        parts.append("</g>")
        parts.append(
            _svg_text(
                max(x50, x95) + 12, y + 4, f"{p50:.0f} / {p95:.0f} 秒", fill="var(--text-secondary)"
            )
        )
    parts.append("</svg>")
    return "".join(parts)


ERROR_KIND_LABELS = {
    "connection": "连接被切",
    "upstream": "上游返回失败",
    "scorer": "评分器失败",
    "missing": "日志里没有该题",
    "other": "其他",
}


def is_lossy(row: dict) -> bool:
    """True when this cell lost more than `LOSSY_ERROR_RATE` of its planned samples."""
    rate = _number(row.get("error_rate"))
    return rate is not None and rate > LOSSY_ERROR_RATE


def _error_tooltip(row: dict) -> str:
    """Hover text on the loss cell: how many samples went missing and in what shape."""
    raw = row.get("error_kinds") or ""
    kinds = json.loads(raw) if raw else {}
    if not kinds:
        return "本组没有损耗，准确率分母等于计划题数。"
    detail = "、".join(
        f"{ERROR_KIND_LABELS.get(kind, kind)} {count} 题" for kind, count in sorted(kinds.items())
    )
    return (
        f"计划 {row['n_planned']} 题，成功评分 {row['n_scored']} 题；"
        f"损耗构成：{detail}。这些题不计入准确率的分子和分母。"
    )


def _wider_cap(row: dict, group: list[dict]) -> bool:
    """True when this row ran with a larger output cap than the smallest one in its benchmark.

    Used only to highlight the cell. The comparison is against the group minimum rather than a
    hardcoded number so that it keeps working if the caps in scripts/run_calibration.sh change.
    """
    caps = [int(r["max_tokens"]) for r in group if str(r.get("max_tokens") or "").isdigit()]
    if not caps or not str(row.get("max_tokens") or "").isdigit():
        return False
    return int(row["max_tokens"]) > min(caps)


def _table(rows: list[dict]) -> str:
    head = [
        "变体",
        "输出上限",
        "准确率",
        "95% 区间",
        "计划题数",
        "出错题数",
        "损耗率",
        "总成本",
        "每题成本",
        "每答对成本",
        "中位耗时",
        "p95 耗时",
    ]
    parts = ["<table><thead><tr>"]
    parts += [f"<th>{esc(column)}</th>" for column in head]
    parts.append("</tr></thead><tbody>")
    for row in rows:
        fusion = row["is_fusion"] == "true"
        swatch = (
            '<span class="swatch fusion"></span>'
            if fusion
            else '<span class="swatch fixed"></span>'
        )
        lossy = is_lossy(row)
        classes = " ".join(
            filter(None, ["fusion" if fusion else "fixed", "lossy" if lossy else ""])
        )
        parts.append(f'<tr class="{classes}">')
        # The low-confidence badge rides the variant name, so the warning travels with the row
        # even when the table is read on a narrow screen and the loss column scrolls out of view.
        badge = (
            '<span class="badge" title="损耗超过 5%，本组结论可信度低">损耗高</span>'
            if lossy
            else ""
        )
        parts.append(f"<td>{swatch}{esc(variant_label(row['model']))}{badge}</td>")
        # max_tokens is per-row because the matrix is no longer uniform: the Fusion routes run
        # GPQA and LCB at a wider cap than the fixed models. Reading the cost or the p95 of two
        # rows against each other without seeing this column would be misleading.
        cap = row.get("max_tokens") or ""
        cap_cell = f"{int(cap):,}" if str(cap).isdigit() else "—"
        cap_class = ' class="wider-cap"' if _wider_cap(row, rows) else ""
        parts.append(f"<td{cap_class}>{esc(cap_cell)}</td>")
        # Denominator is the scored count, not the planned count: errored samples never produced
        # an answer, so they are neither right nor wrong.
        parts.append(f"<td>{fmt_pct(row['accuracy'])}（{row['n_correct']}/{row['n_scored']}）</td>")
        parts.append(f"<td>{fmt_pct(row['wilson_low'])}–{fmt_pct(row['wilson_high'])}</td>")
        parts.append(f"<td>{esc(row['n_planned'])}</td>")
        parts.append(f"<td>{esc(row['n_error'])}</td>")
        cell_class = ' class="lossy-cell"' if lossy else ""
        parts.append(
            f'<td{cell_class} title="{esc(_error_tooltip(row))}">{fmt_pct(row["error_rate"])}</td>'
        )
        parts.append(f"<td>{fmt_money(row['cost_usd_settled'], 4)}</td>")
        parts.append(f"<td>{fmt_money(row['cost_usd_per_sample'], 4)}</td>")
        parts.append(f"<td>{fmt_money(row['cost_per_correct_usd'], 4)}</td>")
        parts.append(f"<td>{fmt(row['latency_p50_s'], 1)}</td>")
        parts.append(f"<td>{fmt(row['latency_p95_s'], 1)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _price_table(rows: list[dict]) -> str:
    priced = [row for row in rows if _number(row.get("cost_usd_estimated_public")) is not None]
    if not priced:
        return ""
    parts = [
        "<table><thead><tr><th>变体</th><th>平台结算成本</th><th>按公开价估算</th>"
        "<th>差异</th></tr></thead><tbody>"
    ]
    for row in priced:
        settled = _number(row.get("cost_usd_settled"))
        estimate = _number(row["cost_usd_estimated_public"])
        diff = "—" if settled is None else f"{(settled - estimate) / estimate * 100:+.0f}%"
        parts.append(
            f"<tr><td>{esc(variant_label(row['model']))}</td>"
            f"<td>{fmt_money(row.get('cost_usd_settled'), 4)}</td>"
            f"<td>{fmt_money(estimate, 4)}</td><td>{esc(diff)}</td></tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _routing_block(rows: list[dict]) -> str:
    parts = []
    for row in rows:
        if not is_fusion(row["model"]):
            continue
        routing = (
            json.loads(row["routing_selected_models"]) if row["routing_selected_models"] else {}
        )
        if routing:
            detail = "、".join(f"{model} {count} 题" for model, count in sorted(routing.items()))
        else:
            detail = "响应中没有 selected_model 字段，无法看到实际路由目标"
        parts.append(
            f'<p class="kv">{esc(row["benchmark_label"])} · {esc(variant_label(row["model"]))}：'
            f"<b>{esc(detail)}</b></p>"
        )
    return "".join(parts)


def _comparison_table(rows: list[dict], baselines: list[str]) -> str:
    """One compact table per benchmark: Fusion auto vs each baseline, one row per pair."""
    by_model = {row["model"]: row for row in rows}
    fusion = by_model.get("apigo/lyra-auto")
    if not fusion:
        return ""
    body_rows = []
    for baseline_name in baselines:
        baseline = by_model.get(baseline_name)
        if not baseline:
            continue
        acc_f, acc_b = _number(fusion["accuracy"]), _number(baseline["accuracy"])
        diff = "—" if acc_f is None or acc_b is None else f"{(acc_f - acc_b) * 100:+.0f}"
        cost_f, cost_b = (
            _number(fusion["cost_usd_per_sample"]),
            _number(baseline["cost_usd_per_sample"]),
        )
        cost_ratio = "—" if not cost_f or not cost_b else f"{cost_f / cost_b:.2f}×"
        p50_f, p50_b = _number(fusion["latency_p50_s"]), _number(baseline["latency_p50_s"])
        latency_ratio = "—" if not p50_f or not p50_b else f"{p50_f / p50_b:.2f}×"
        body_rows.append(
            f"<tr><td>Fusion auto vs {esc(variant_label(baseline_name))}</td>"
            f"<td>{diff}</td><td>{cost_ratio}</td><td>{latency_ratio}</td></tr>"
        )
    if not body_rows:
        return ""
    head = "<tr><th>对比</th><th>准确率差（百分点）</th><th>每题成本比</th><th>中位耗时比</th></tr>"
    return f"<table><thead>{head}</thead><tbody>{''.join(body_rows)}</tbody></table>"


def _baseline_table(block: dict) -> str:
    """One benchmark: BestSingle / Oracle / Random / each Fusion variant, one row each."""
    front = block.get("pareto_front") or []
    best, oracle = block["best_single"], block["oracle"]
    best_accuracy = _number(best.get("accuracy"))
    best_cost = _number(best.get("cost_usd_per_sample"))
    oracle_accuracy = _number(oracle.get("accuracy"))
    span = (
        None
        if oracle_accuracy is None or best_accuracy is None
        else oracle_accuracy - best_accuracy
    )

    def headroom_cell(accuracy: float | None) -> str:
        if span is None or accuracy is None or best_accuracy is None:
            return "—"
        if abs(span) < 1e-12:
            return "无空间"
        return f"{(accuracy - best_accuracy) / span * 100:.0f}%"

    def cost_cell(cost: float | None) -> str:
        return "—" if not cost or not best_cost else f"{cost / best_cost:.2f}×"

    entries = [
        (f"BestSingle：{best.get('label', best.get('model', ''))}", best_accuracy, best_cost),
        (BASELINE_LABELS["oracle"], oracle_accuracy, _number(oracle.get("cost_usd_per_sample"))),
        (
            BASELINE_LABELS["random"],
            _number(block["random_uniform"].get("accuracy")),
            _number(block["random_uniform"].get("cost_usd_per_sample")),
        ),
    ]
    body = []
    for label, accuracy, cost in entries:
        body.append(
            f"<tr><td>{esc(label)}</td><td>{fmt_pct(accuracy)}</td>"
            f"<td>{fmt_money(cost)}</td><td>{esc(cost_cell(cost))}</td>"
            f"<td>{esc(headroom_cell(accuracy))}</td>"
            f"<td>{esc(classify_vs_mix(front, cost, accuracy))}</td></tr>"
        )
    for variant in block.get("variants", []):
        accuracy = _number(variant.get("accuracy"))
        cost = _number(variant.get("cost_usd_per_sample"))
        headroom = "无空间" if variant.get("headroom_is_empty") else headroom_cell(accuracy)
        gap = variant.get("gap_to_oracle_pp")
        gap_text = "" if gap is None else f"（距 Oracle {gap:+.0f} 个百分点）"
        body.append(
            f'<tr class="fusion"><td><span class="swatch fusion"></span>'
            f"{esc(variant['label'])}{esc(gap_text)}</td>"
            f"<td>{fmt_pct(accuracy)}</td><td>{fmt_money(cost)}</td>"
            f"<td>{esc(cost_cell(cost))}</td><td>{esc(headroom)}</td>"
            f"<td>{esc(variant.get('above_random_mix', '—'))}</td></tr>"
        )
    head = (
        "<tr><th>变体</th><th>准确率</th><th>每题成本</th><th>相对 BestSingle 成本</th>"
        "<th>吃掉的 Oracle 空间</th><th>是否高于随机混合线</th></tr>"
    )
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def _baseline_section(baselines: dict[str, dict]) -> list[str]:
    """The 路由基线对比 block: explanation, per-benchmark shares, one table per benchmark."""
    if not baselines:
        return []
    parts = [
        "<h2>路由基线对比</h2>",
        '<section class="card">',
        '<p class="note">BestSingle 是本赛道准确率最高的固定模型（并列时取更便宜的那个），'
        "代表不做路由、全程只用一个模型。</p>",
        '<p class="note">Oracle 是逐题挑出答对且最便宜的固定模型，是任何路由器的理论上界；'
        "Random 是逐题在固定模型里均匀随机挑一个。</p>",
        '<p class="note">路由器至少要压过随机混合线，才谈得上会选模型。</p>',
        '<p class="note">候选池只含固定模型（Fusion 变体不作自己的基线）；'
        "出错的题（连接被切、上游失败、评分器崩）没有答案，不计入任何一方的分子分母，"
        "Oracle / Random 的分母是「至少有一个固定模型评出分」的题数。"
        f"{esc(COST_FALLBACK_NOTE)}</p>",
    ]
    for benchmark in sorted(baselines, key=lambda name: _sort_key((name, ""))):
        block = baselines[benchmark]
        fallback = (block.get("cost_fallback_samples") or {}).get("total", 0)
        parts.append(f"<h3>{esc(block['benchmark_label'])}</h3>")
        parts.append(
            '<p class="kv">可路由题占比 <b>'
            f"{fmt_pct(block['oracle'].get('routable_share'))}</b>"
            "｜无人答对占比 <b>"
            f"{fmt_pct(block['oracle'].get('unsolved_share'))}</b>"
            f"｜可评测题数 <b>{esc(block.get('n_evaluable', ''))}</b>"
            f" / 计划 <b>{esc(block.get('n_planned', ''))}</b>"
            f"｜回退到运行平均成本的逐题记录 <b>{esc(fallback)}</b> 条</p>"
        )
        parts.append('<p class="note">可路由题越少，这套题越看不出路由能力。</p>')
        if len(block.get("pareto_front") or []) < 2:
            parts.append(
                '<p class="note">本赛道的帕累托前沿只有一个点（最便宜的固定模型同时也最准），'
                "随机混合线退化成这一个点，散点图里不画虚线，判定仍按该点比较。</p>"
            )
        parts.append(_baseline_table(block))
    parts.append("</section>")
    return parts


def render_html(rows: list[dict], meta: dict, baselines: dict[str, dict] | None = None) -> str:
    benchmarks: dict[str, list[dict]] = {}
    for row in rows:
        benchmarks.setdefault(row["benchmark"], []).append(row)

    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(meta['title'])}</title><style>{_css()}</style></head><body>",
        '<div class="viz-root">',
        f"<h1>{esc(meta['title'])}</h1>",
        f'<p class="sub">运行集：{esc(meta["run_set"])}｜生成时间：{esc(meta["generated"])}'
        f"｜每个格子 {esc(meta['samples_per_cell'])} 题｜共 {esc(meta['n_cells'])} 个"
        "（赛道 × 变体）组合</p>",
        '<p class="note">这是校准跑的数据，只用来检查流程、成本口径和耗时，不能当作模型排名；'
        "每格样本量很小，差异只作描述，不作结论。</p>",
        '<p class="note">计分口径：准确率的分母是<b>成功评分的题数</b>。因连接被切、上游返回失败或'
        "评分器崩溃而没有答案的题，既不算答对也不算答错，一律从分子分母里剔除——这些是基础设施故障，"
        "不是模型的能力。每组单独列出计划题数、出错题数和损耗率；"
        f"损耗超过 {LOSSY_ERROR_RATE:.0%} 的组标为「损耗高」，其准确率只建立在剩下的题上，"
        "可信度低，不要和跑干净的组直接比。</p>",
        '<p class="note">口径不对称，必须先看：GPQA 与 LiveCodeBench 上，三个 Fusion 路由的'
        "输出上限是 32768 tokens，五个固定模型仍是 16384（IFEval 两边都是 8192）。原因是 16384 会"
        "在推理中途截断 Fusion，正文返回空，网关记为 lyra_execution_failed；把上限放宽到 32768 后，"
        "在 lyra-auto 原先失败的 12 道 GPQA 题上有 7 道恢复出可评分的答案。再往上放没有意义："
        "上游单次尝试另有约 297 秒的硬期限，65536 的失败时刻和 32768 完全一样。"
        "代价是这两个赛道的 Fusion 格子和固定模型格子<b>在成本和耗时长尾上不可直接比较</b>，"
        "准确率也是在更宽的预算下取得的。下面每张表都有「输出上限」一列，放宽过的格子标了下划线。</p>",
        '<div class="legend">'
        '<span><span class="swatch fusion"></span>Fusion 路由（实心）</span>'
        '<span><span class="swatch fixed"></span>固定模型（空心）</span>'
        f"<span>{LEGEND_GLYPHS['best']} BestSingle</span>"
        f"<span>{LEGEND_GLYPHS['oracle']} Oracle（理论上界）</span>"
        f"<span>{LEGEND_GLYPHS['random']} 随机（均匀）</span>"
        f"<span>{LEGEND_GLYPHS['mix']} 随机混合线</span>"
        f"<span>{LEGEND_GLYPHS['lossy']} 损耗 &gt;{LOSSY_ERROR_RATE:.0%}（可信度低）</span></div>",
    ]
    baselines = baselines or {}

    for benchmark in sorted(benchmarks, key=lambda name: _sort_key((name, ""))):
        group = benchmarks[benchmark]
        label = group[0]["benchmark_label"]
        parts.append(f"<h2>{esc(label)}</h2>")
        parts.append('<section class="card">')
        parts.append("<h3>越靠左上越好：便宜且答得准</h3>")
        parts.append(
            '<p class="note">竖线是 95% Wilson 置信区间，按成功评分的题数算，'
            "所以损耗大的组区间更宽——这正是它证据更少的意思。虚线圈出的点损耗超过 "
            f"{LOSSY_ERROR_RATE:.0%}。</p>"
        )
        parts.append(scatter_svg(group, baselines.get(benchmark)))
        parts.append(_table(group))
        price_table = _price_table(group)
        if price_table:
            parts.append("<h3>结算成本与公开价估算的对照</h3>")
            parts.append(
                '<p class="note">只有固定模型有公开价；Fusion 路由没有公开价，'
                "因此只给平台结算金额。</p>"
            )
            parts.append(price_table)
        parts.append("</section>")

    parts.extend(_baseline_section(baselines))

    parts.append("<h2>耗时对比</h2>")
    parts.append(
        '<p class="note">实心点是中位耗时，空心点是 p95（最慢的那几题）。'
        "耗时只统计正常完成的题；连接被中断的题记为出错，"
        "既不计入耗时，也不计入准确率。</p>"
    )
    for benchmark in sorted(benchmarks, key=lambda name: _sort_key((name, ""))):
        group = benchmarks[benchmark]
        parts.append('<section class="card">')
        parts.append(f"<h3>{esc(group[0]['benchmark_label'])}</h3>")
        parts.append(latency_svg(group))
        parts.append("</section>")

    parts.append("<h2>Fusion 实际路由到了哪些模型</h2>")
    parts.append('<section class="card">')
    routing = "".join(
        _routing_block(benchmarks[b]) for b in sorted(benchmarks, key=lambda n: _sort_key((n, "")))
    )
    parts.append(routing or '<p class="kv">本次没有 Fusion 变体。</p>')
    parts.append(
        '<p class="note">只有 lyra-auto 在响应里返回了所选模型；budget 与 quality 没有暴露 '
        "selected_model，所以无法从日志看出它们路由到了谁。</p>"
    )
    parts.append("</section>")

    parts.append("<h2>Fusion 与固定模型的逐项对比</h2>")
    parts.append('<section class="card">')
    parts.append(
        '<p class="note">怎么读：准确率差为正表示 Fusion auto 更准；成本比、耗时比大于 1× '
        "表示 Fusion auto 更贵或更慢。以下均为观察到的数字，每格样本量小，不作结论。</p>"
    )
    for benchmark in sorted(benchmarks, key=lambda name: _sort_key((name, ""))):
        group = benchmarks[benchmark]
        table = _comparison_table(
            group, ["gpt-6-astra", "gpt-5.6-sol", "claude-sonnet-5", "claude-opus-5"]
        )
        if table:
            parts.append(f"<h3>{esc(group[0]['benchmark_label'])}</h3>")
            parts.append(table)
    parts.append("</section>")

    parts.append("<h2>导出数据</h2>")
    parts.append(
        '<div class="links"><a href="report.csv" download>report.csv（每格一行）</a>'
        '<a href="report.json" download>report.json</a>'
        '<a href="samples.csv" download>samples.csv（每题一行）</a></div>'
    )
    parts.append(
        '<p class="note">成本口径：平台按模型 + 运行时间窗归集，Fusion 路由是独立账单行，'
        "已含其内部调用；逐题成本按请求时长近似匹配，匹配不上的留空，不写 0。</p>"
    )
    parts.append(
        '<p class="note">表里的「每题成本」= 该 run 的结算总额 ÷ 成功评分题数，和准确率同分母，'
        "读作「拿到一个可用答案花了多少钱」。分子<b>保留</b>为失败请求付掉的钱："
        "Lyra 在流内返回 lyra_execution_failed 时外层仍是 HTTP 200，平台照常计费；"
        "而连接被中断的请求不结算，本身就是 0 美元。所以损耗大的组每题成本偏高，"
        "这是真实花掉的钱，不是口径错误。report.csv 里另有一列 "
        "<b>cost_usd_per_planned_sample</b>（结算总额 ÷ 计划题数），即旧口径，供对照。</p>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


# --------------------------------------------------------------------------- writers


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / ".local/inspect-logs/calibration")
    parser.add_argument(
        "--platform", type=Path, default=REPO_ROOT / ".local/platform-logs-today.json"
    )
    parser.add_argument(
        "--prices", type=Path, default=REPO_ROOT / ".local/acceptance/ifeval-prices.json"
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / ".local/inspect-report")
    parser.add_argument("--run-set", default="", help="name shown in the report header")
    parser.add_argument("--tail-seconds", type=int, default=DEFAULT_TAIL_SECONDS)
    args = parser.parse_args(argv)

    logs = sorted(args.logs.glob("*.eval")) if args.logs.is_dir() else [args.logs]
    if not logs:
        print(f"no .eval logs under {args.logs}", file=sys.stderr)
        return 2

    summary = _load_inspect_summary()
    sample_rows = [row for path in logs for row in summary.rows_for(path)]
    runs = collect_runs(logs)
    entries = load_platform_entries(args.platform)
    prices = load_public_prices(args.prices)
    if not entries:
        print(
            f"platform export missing ({args.platform}); cost columns stay empty", file=sys.stderr
        )

    report, detailed = build_rows(
        sample_rows, runs, entries, prices, tail_seconds=args.tail_seconds
    )

    baselines = compute_baselines(report, detailed)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "runs.jsonl").write_text(
        "".join(json.dumps(run, ensure_ascii=False) + "\n" for run in runs), encoding="utf-8"
    )
    (args.out / "report.json").write_text(
        json.dumps({"rows": report, "baselines": baselines}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(args.out / "report.csv", [*report, *baseline_csv_rows(baselines)], REPORT_FIELDS)
    write_csv(
        args.out / "samples.csv",
        detailed,
        [*summary.FIELDS, *SAMPLE_EXTRA_FIELDS],
    )

    dates = sorted({run["started"][:10] for run in runs if run.get("started")})
    meta = {
        "title": "Fusion 路由与固定模型对比：成本、耗时、准确率",
        "run_set": args.run_set or args.logs.name,
        "generated": dates[-1] if dates else "",
        "samples_per_cell": max((row["n_planned"] for row in report), default=0),
        "n_cells": len(report),
    }
    (args.out / "report.html").write_text(render_html(report, meta, baselines), encoding="utf-8")
    print(f"{len(report)} rows, {len(detailed)} samples -> {args.out / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
