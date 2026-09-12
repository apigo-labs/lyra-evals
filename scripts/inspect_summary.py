"""Flatten Inspect AI .eval logs into per-sample rows for cost, latency and routing evidence.

Rows carry the Gateway response id (Lyra execution id) so billing can be reconciled through
the existing Platform log audit, plus Lyra's routing block when present (router/answer model,
difficulty, task type, policy version). No prompts, answers or credentials are exported.

Usage (uses `inspect log dump`, so run through the isolated tool environment):
  uv run python scripts/inspect_summary.py .local/inspect-logs/*.eval --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

INSPECT_AI_VERSION = "0.3.263"

FIELDS = [
    "log",
    "task",
    "model",
    "reasoning_effort",
    "sample_id",
    "epoch",
    "score",
    "score_detail",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "stop_reason",
    "total_time_s",
    "working_time_s",
    "response_id",
    "served_model",
    "lyra_selected_model",
    "lyra_router_model",
    "lyra_difficulty",
    "lyra_task_type",
    "lyra_mode",
    "lyra_policy_version",
    "lyra_attempt_count",
    "error",
]


def dump(path: Path) -> dict:
    proc = subprocess.run(
        ["uvx", "--from", f"inspect-ai=={INSPECT_AI_VERSION}", "inspect", "log", "dump", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


def score_of(sample: dict) -> tuple[str, str]:
    scores = sample.get("scores") or {}
    if not scores:
        return "", ""
    name, entry = next(iter(scores.items()))
    value = entry.get("value")
    if isinstance(value, dict):
        # IFEval: prompt-level strict is the headline metric.
        primary = value.get("prompt_level_strict")
        return ("" if primary is None else str(int(bool(primary)))), json.dumps(
            value, sort_keys=True
        )
    if isinstance(value, str):
        return ("1" if value == "C" else "0" if value in {"I", "P"} else value), name
    return (str(value) if value is not None else ""), name


def rows_for(path: Path) -> list[dict]:
    log = dump(path)
    eval_meta = log.get("eval", {})
    task = eval_meta.get("task")
    model = eval_meta.get("model")
    effort = (eval_meta.get("model_generate_config") or {}).get("reasoning_effort")
    rows = []
    for sample in log.get("samples") or []:
        model_events = [e for e in sample.get("events") or [] if e.get("event") == "model"]
        usage = {}
        response = {}
        stop = ""
        served = ""
        if model_events:
            first = model_events[0]
            output = first.get("output") or {}
            usage = output.get("usage") or {}
            served = output.get("model") or ""
            choices = output.get("choices") or []
            stop = choices[0].get("stop_reason", "") if choices else ""
            response = (first.get("call") or {}).get("response") or {}
        lyra = response.get("lyra") or {}
        routing = lyra.get("routing") or {}
        router = next(
            (
                p.get("model")
                for p in lyra.get("participating_models") or []
                if p.get("role") == "router"
            ),
            "",
        )
        score, detail = score_of(sample)
        rows.append(
            {
                "log": path.name,
                "task": task,
                "model": model,
                "reasoning_effort": effort or "",
                "sample_id": sample.get("id"),
                "epoch": sample.get("epoch"),
                "score": score,
                "score_detail": detail,
                "input_tokens": usage.get("input_tokens", ""),
                "output_tokens": usage.get("output_tokens", ""),
                "reasoning_tokens": usage.get("reasoning_tokens", ""),
                "cache_read_tokens": usage.get("input_tokens_cache_read", ""),
                "cache_write_tokens": usage.get("input_tokens_cache_write", ""),
                "stop_reason": stop,
                "total_time_s": sample.get("total_time", ""),
                "working_time_s": sample.get("working_time", ""),
                "response_id": response.get("id", ""),
                "served_model": served,
                "lyra_selected_model": routing.get("selected_model", ""),
                "lyra_router_model": router,
                "lyra_difficulty": routing.get("difficulty", ""),
                "lyra_task_type": routing.get("task_type", ""),
                "lyra_mode": routing.get("mode", ""),
                "lyra_policy_version": routing.get("policy_version", ""),
                "lyra_attempt_count": lyra.get("attempt_count", ""),
                "error": (sample.get("error") or {}).get("message", "")
                if sample.get("error")
                else "",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--csv", type=Path, help="write rows to this CSV instead of stdout table")
    args = parser.parse_args()
    rows = [row for path in args.logs for row in rows_for(path)]
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{len(rows)} rows -> {args.csv}")
        return 0
    for row in rows:
        print(
            f"{row['task']:<28} {row['model']:<36} {row['reasoning_effort']!s:<8} "
            f"{row['sample_id']!s:<20} score={row['score']:<3} in={row['input_tokens']:<6} "
            f"out={row['output_tokens']:<6} reason={row['reasoning_tokens']!s:<6} "
            f"t={row['total_time_s']!s:<8} routed={row['lyra_selected_model'] or '-'} "
            f"diff={row['lyra_difficulty'] or '-'} id={row['response_id']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
