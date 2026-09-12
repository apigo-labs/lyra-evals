"""Build the cost / latency / accuracy comparison report from Inspect AI calibration logs.

Reads the `.eval` logs of one run set, flattens them with `scripts/inspect_summary.py`, folds in
the Platform billing export, and writes `report.json`, `report.csv`, `samples.csv` and a
self-contained `report.html` (inline CSS, hand-rendered SVG, no network at open time).

Cost attribution rule: Platform bills every Gateway request as its own line item and the Fusion
routing models (`apigo/lyra-*`) appear as their own line items with their own cost, so no
sub-call summing is needed. Inspect does not record the Gateway request id, so a run's cost is
the sum of the export entries whose model equals the variant's model and whose time falls inside
[run started, run completed + tail]. Per-sample cost is an approximation matched by request
duration and is left empty when it cannot be matched one to one.

Usage:
  uv run python scripts/inspect_report.py --logs .local/inspect-logs/calibration \
      --platform .local/platform-logs-today.json --out .local/inspect-report
"""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
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
    "harness",
    "is_fusion",
    "n_planned",
    "n_scored",
    "n_correct",
    "n_error",
    "accuracy",
    "wilson_low",
    "wilson_high",
    "cost_usd_settled",
    "cost_billed_requests",
    "cost_usd_per_sample",
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
    """95% Wilson score interval; errors stay in the denominator, so `total` is the planned n."""
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
        accuracy = n_correct / n_planned if n_planned else None
        low, high = wilson_interval(n_correct, n_planned) if n_planned else (None, None)

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
                "harness": "direct",
                "is_fusion": str(is_fusion(model)).lower(),
                "n_planned": n_planned,
                "n_scored": n_scored,
                "n_correct": n_correct,
                "n_error": n_error,
                "accuracy": _round(accuracy, 4),
                "wilson_low": _round(low, 4),
                "wilson_high": _round(high, 4),
                "cost_usd_settled": "" if cost_total is None else round(cost_total, 6),
                "cost_billed_requests": billed,
                "cost_usd_per_sample": ""
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
    },
}

CSS = """
:root { color-scheme: light dark; }
.viz-root {
  --page: %(l_page)s; --surface: %(l_surface)s; --text-primary: %(l_primary)s;
  --text-secondary: %(l_secondary)s; --muted: %(l_muted)s; --grid: %(l_grid)s;
  --axis: %(l_axis)s; --fusion: %(l_fusion)s; --fixed: %(l_fixed)s; --border: %(l_border)s;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    --page: %(d_page)s; --surface: %(d_surface)s; --text-primary: %(d_primary)s;
    --text-secondary: %(d_secondary)s; --muted: %(d_muted)s; --grid: %(d_grid)s;
    --axis: %(d_axis)s; --fusion: %(d_fusion)s; --fixed: %(d_fixed)s; --border: %(d_border)s;
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
    """Rough glyph-width estimate for ASCII labels in a system-ui sans-serif at `size`."""
    return len(text) * size * 0.58


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


def scatter_svg(rows: list[dict]) -> str:
    """Quality vs cost: x = settled cost per sample, y = accuracy with Wilson bars."""
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
    costs = [_number(row["cost_usd_per_sample"]) for row in points]
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

    point_meta = []
    for row in points:
        fusion = row["is_fusion"] == "true"
        color = "var(--fusion)" if fusion else "var(--fixed)"
        x = x_axis.to_pixel(_number(row["cost_usd_per_sample"]))
        y = y_axis.to_pixel(_number(row["accuracy"]))
        y_low = y_axis.to_pixel(_number(row["wilson_low"]) or 0.0)
        y_high = y_axis.to_pixel(_number(row["wilson_high"]) or 0.0)
        tip = (
            f"{variant_label(row['model'])}｜准确率 {fmt_pct(row['accuracy'])}"
            f"（{row['n_correct']}/{row['n_planned']}）"
            f"｜每题 {fmt_money(row['cost_usd_per_sample'])}"
            f"｜p50 {fmt(row['latency_p50_s'], 1, ' 秒')}"
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
        parts.append("</g>")
        point_meta.append({"x": x, "y": y, "label": variant_label(row["model"])})

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


def _table(rows: list[dict]) -> str:
    head = [
        "变体",
        "准确率",
        "95% 区间",
        "总成本",
        "每题成本",
        "每答对成本",
        "中位耗时",
        "p95 耗时",
        "出错题数",
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
        parts.append(f'<tr class="{"fusion" if fusion else "fixed"}">')
        parts.append(f"<td>{swatch}{esc(variant_label(row['model']))}</td>")
        parts.append(
            f"<td>{fmt_pct(row['accuracy'])}（{row['n_correct']}/{row['n_planned']}）</td>"
        )
        parts.append(f"<td>{fmt_pct(row['wilson_low'])}–{fmt_pct(row['wilson_high'])}</td>")
        parts.append(f"<td>{fmt_money(row['cost_usd_settled'], 4)}</td>")
        parts.append(f"<td>{fmt_money(row['cost_usd_per_sample'], 4)}</td>")
        parts.append(f"<td>{fmt_money(row['cost_per_correct_usd'], 4)}</td>")
        parts.append(f"<td>{fmt(row['latency_p50_s'], 1)}</td>")
        parts.append(f"<td>{fmt(row['latency_p95_s'], 1)}</td>")
        parts.append(f"<td>{row['n_error']}</td>")
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


def render_html(rows: list[dict], meta: dict) -> str:
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
        '<div class="legend">'
        '<span><span class="swatch fusion"></span>Fusion 路由（实心）</span>'
        '<span><span class="swatch fixed"></span>固定模型（空心）</span></div>',
    ]

    for benchmark in sorted(benchmarks, key=lambda name: _sort_key((name, ""))):
        group = benchmarks[benchmark]
        label = group[0]["benchmark_label"]
        parts.append(f"<h2>{esc(label)}</h2>")
        parts.append('<section class="card">')
        parts.append("<h3>越靠左上越好：便宜且答得准</h3>")
        parts.append('<p class="note">竖线是 95% 置信区间，n=10 时区间很宽，属正常。</p>')
        parts.append(scatter_svg(group))
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

    parts.append("<h2>耗时对比</h2>")
    parts.append(
        '<p class="note">实心点是中位耗时，空心点是 p95（最慢的那几题）。'
        "耗时只统计正常完成的题；本次有若干题在 600 秒的客户端超时上断开连接，"
        "它们记为出错，计入准确率分母但不计入耗时。</p>"
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
        table = _comparison_table(group, ["gpt-6-astra", "gpt-5.6-sol", "claude-sonnet-5", "claude-opus-5"])
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

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "runs.jsonl").write_text(
        "".join(json.dumps(run, ensure_ascii=False) + "\n" for run in runs), encoding="utf-8"
    )
    (args.out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(args.out / "report.csv", report, REPORT_FIELDS)
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
    (args.out / "report.html").write_text(render_html(report, meta), encoding="utf-8")
    print(f"{len(report)} rows, {len(detailed)} samples -> {args.out / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
