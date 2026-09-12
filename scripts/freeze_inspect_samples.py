#!/usr/bin/env python3
"""Freeze stratified calibration/main sample sets for the Inspect AI direct-api profile.

See `.local/designs/fusion-roadshow-evaluation.md` section 3. This produces, per
benchmark, two disjoint, stratified, seed-fixed sets of *official* sample ids
(the ids Inspect's `--sample-id` expects), so every model variant answers the
exact same cases:

  - ifeval:        id = the dataset ``key`` field (an integer).
  - gpqa_diamond:  id = the CSV ``Record ID`` field.
  - livecodebench: id = the pack row's case id (== the official question id).

Historical case ids already used in real runs (`.local/console/console.sqlite3`)
or already exercised in `.local/inspect-logs/*.eval` are excluded before sampling.

No network calls, no model calls. Deterministic given the same pack contents,
exclusion sources, seed, and sizes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vohu_evals.suites.packs import load_pack

DEFAULT_SEED = 20260909
DEFAULT_SUITES = ("ifeval", "gpqa", "livecodebench")
DEFAULT_SIZES = {
    "ifeval": {"calibration": 10, "main": 120},
    "gpqa": {"calibration": 10, "main": 60},
    "livecodebench": {"calibration": 10, "main": 120},
}
INSPECT_VERSION = "0.3.263"
# Inspect eval log `eval.task` -> our suite name, for reading historical sample ids.
LOG_TASK_TO_SUITE = {
    "inspect_evals/ifeval": "ifeval",
    "inspect_evals/gpqa_diamond": "gpqa",
}


class SuiteUnavailable(Exception):
    """Raised when a suite's official id (or stratum) field cannot be recovered."""


# ---------------------------------------------------------------------------
# Generic stratified sampling engine (suite-agnostic; unit-tested directly).
# ---------------------------------------------------------------------------


def apportion(populations: dict[str, int], total: int) -> dict[str, int]:
    """Largest-remainder apportionment: counts sum exactly to `total`.

    Deterministic: ties in fractional remainder broken by sorted label. Raises
    if `total` exceeds the summed population (never silently pad a shortfall).
    """
    keys = sorted(populations)
    grand = sum(populations[k] for k in keys)
    if total < 0:
        raise ValueError("total must be non-negative")
    if total > grand:
        raise ValueError(f"requested {total} exceeds available population {grand}")
    if grand == 0 or total == 0:
        return {k: 0 for k in keys}
    exact = {k: populations[k] * total / grand for k in keys}
    base = {k: int(exact[k]) for k in keys}
    remainder = total - sum(base.values())
    order = sorted(keys, key=lambda k: (-(exact[k] - base[k]), k))
    for k in order[:remainder]:
        base[k] += 1
    return base


def build_strata(
    pairs: list[tuple[str, str | None]], field_name: str
) -> tuple[dict[str, list[str]], str, str | None]:
    """Group ids by stratum label; fall back to a single uniform stratum if the
    label is missing for any row (partially or fully) rather than inventing labels.
    """
    missing = sum(1 for _, stratum in pairs if stratum is None)
    if missing:
        reason = (
            f"stratum field '{field_name}' missing for {missing}/{len(pairs)} rows; "
            "falling back to uniform sampling"
        )
        return {"uniform": [id_ for id_, _ in pairs]}, "uniform", reason
    strata: dict[str, list[str]] = {}
    for id_, stratum in pairs:
        strata.setdefault(str(stratum), []).append(id_)
    return strata, field_name, None


def draw_stratified(
    strata: dict[str, list[str]], calibration_n: int, main_n: int, seed: int
) -> tuple[list[str], list[str], dict[str, dict]]:
    """Draw disjoint calibration/main id sets from each stratum.

    Strata are processed in sorted label order; ids within a stratum are sorted
    before drawing; one `random.Random(seed)` instance is shared and consumed in
    that fixed order, so identical inputs always yield identical output.
    """
    populations = {label: len(ids) for label, ids in strata.items()}
    calibration_alloc = apportion(populations, calibration_n)
    remaining = {label: populations[label] - calibration_alloc[label] for label in populations}
    main_alloc = apportion(remaining, main_n)

    rng = random.Random(seed)
    calibration_ids: list[str] = []
    main_ids: list[str] = []
    per_stratum: dict[str, dict] = {}
    for label in sorted(strata):
        ids_sorted = sorted(strata[label])
        k = calibration_alloc[label] + main_alloc[label]
        drawn = rng.sample(ids_sorted, k) if k else []
        cal_part = drawn[: calibration_alloc[label]]
        main_part = drawn[calibration_alloc[label] :]
        calibration_ids.extend(cal_part)
        main_ids.extend(main_part)
        pop = populations[label]
        per_stratum[label] = {
            "population": pop,
            "calibration_count": calibration_alloc[label],
            "main_count": main_alloc[label],
            "calibration_probability": (calibration_alloc[label] / pop) if pop else 0.0,
            "main_probability": (main_alloc[label] / pop) if pop else 0.0,
        }
    return sorted(calibration_ids), sorted(main_ids), per_stratum


def freeze_suite(
    pairs: list[tuple[str, str | None]],
    stratum_field: str,
    calibration_n: int,
    main_n: int,
    seed: int,
    excluded_ids: set[str],
) -> dict:
    """Exclude historical ids, stratify, and draw calibration/main sets."""
    excluded_present = {id_ for id_, _ in pairs if id_ in excluded_ids}
    kept = [(id_, stratum) for id_, stratum in pairs if id_ not in excluded_ids]
    dupe_check = [id_ for id_, _ in kept]
    if len(dupe_check) != len(set(dupe_check)):
        raise ValueError("duplicate official ids after exclusion; cannot sample")
    strata, field_used, uniform_reason = build_strata(kept, stratum_field)
    calibration_ids, main_ids, per_stratum = draw_stratified(strata, calibration_n, main_n, seed)
    overlap = set(calibration_ids) & set(main_ids)
    if overlap:
        raise ValueError(f"calibration/main sets not disjoint: {sorted(overlap)[:5]}")
    return {
        "seed": seed,
        "stratum_field": field_used,
        "uniform_reason": uniform_reason,
        "excluded_ids_count": len(excluded_present),
        "population_after_exclusion": len(kept),
        "strata": per_stratum,
        "calibration_ids": calibration_ids,
        "main_ids": main_ids,
    }


# ---------------------------------------------------------------------------
# Suite-specific loaders (read the real frozen packs + source files).
# ---------------------------------------------------------------------------


def load_ifeval_pairs(packs_root: Path) -> tuple[list[tuple[str, str | None]], dict, dict]:
    manifest, rows = load_pack(packs_root, "ifeval")
    pairs: list[tuple[str, str | None]] = []
    coverage: Counter[str] = Counter()
    for row in rows:
        if "key" not in row:
            raise SuiteUnavailable(
                "ifeval pack rows do not carry the official 'key' field; refusing "
                "to invent an id mapping"
            )
        official_id = str(row["key"])
        instruction_ids = row.get("instruction_id_list") or []
        coverage.update(instruction_ids)
        pairs.append((official_id, str(len(instruction_ids))))
    return pairs, manifest, dict(coverage)


def _gpqa_source_csv_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def load_gpqa_pairs(
    packs_root: Path, gpqa_csv: Path | None
) -> tuple[list[tuple[str, str | None]], dict, dict]:
    manifest, rows = load_pack(packs_root, "gpqa")
    coverage: Counter[str] = Counter()

    if rows and "Record ID" in rows[0]:
        pairs = []
        for row in rows:
            domain = row.get("High-level domain")
            if domain:
                coverage[domain] += 1
            pairs.append((str(row["Record ID"]), domain))
        return pairs, manifest, dict(coverage)

    csv_path = gpqa_csv or (packs_root / "downloads" / "gpqa-diamond.csv")
    if not csv_path.exists():
        raise SuiteUnavailable(
            "gpqa pack rows do not carry 'Record ID'/'High-level domain', and no source "
            f"CSV was found at {csv_path} to recover them; refusing to invent an id mapping"
        )
    source_sha = manifest.get("source", {}).get("sha256")
    actual_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if source_sha and actual_sha != source_sha:
        raise SuiteUnavailable(
            f"gpqa source CSV at {csv_path} does not match the pack manifest's declared "
            "source sha256; refusing to trust a positional id mapping against it"
        )
    csv_rows = _gpqa_source_csv_rows(csv_path)
    if len(csv_rows) != len(rows):
        raise SuiteUnavailable(
            f"gpqa source CSV has {len(csv_rows)} rows but the pack has {len(rows)}; "
            "positional id mapping would be unreliable"
        )
    pairs = []
    for row in rows:
        index = int(row["case_id"])
        src = csv_rows[index]
        if "Record ID" not in src:
            raise SuiteUnavailable("gpqa source CSV missing 'Record ID' column")
        official_id = src["Record ID"]
        domain = src.get("High-level domain") or None
        if domain:
            coverage[domain] += 1
        pairs.append((official_id, domain))
    return pairs, manifest, dict(coverage)


def _lcb_source_metadata(packs_root: Path, manifest: dict) -> dict[str, dict]:
    source = manifest.get("source", {})
    revision = source.get("revision")
    artifacts: dict[str, str] = source.get("artifacts", {})
    if not revision or not artifacts:
        raise SuiteUnavailable("livecodebench pack manifest missing source revision/artifacts")
    metadata: dict[str, dict] = {}
    for filename, expected_sha in sorted(artifacts.items()):
        path = packs_root / "downloads" / f"lcb-{revision}-{filename}"
        if not path.exists():
            raise SuiteUnavailable(
                f"livecodebench source file {path} not found; cannot recover difficulty/date"
            )
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise SuiteUnavailable(
                f"livecodebench source file {path} sha256 mismatch against manifest artifacts"
            )
        with path.open() as stream:
            for line in stream:
                if not line.strip():
                    continue
                raw = json.loads(line)
                metadata[str(raw["question_id"])] = {
                    "difficulty": raw.get("difficulty"),
                    "contest_date": raw.get("contest_date"),
                }
    return metadata


def _lcb_stratum_label(meta: dict | None) -> str | None:
    if not meta or not meta.get("difficulty"):
        return None
    difficulty = meta["difficulty"]
    date = meta.get("contest_date")
    if date:
        match = re.match(r"(\d{4})", str(date))
        if match:
            return f"{difficulty}|{match.group(1)}"
    return difficulty


def load_livecodebench_pairs(
    packs_root: Path,
) -> tuple[list[tuple[str, str | None]], dict, dict]:
    manifest, rows = load_pack(packs_root, "livecodebench")
    metadata = _lcb_source_metadata(packs_root, manifest)
    coverage: Counter[str] = Counter()
    pairs = []
    for row in rows:
        official_id = row["case_id"]
        meta = metadata.get(official_id)
        label = _lcb_stratum_label(meta)
        if label:
            coverage[label] += 1
        pairs.append((official_id, label))
    return pairs, manifest, dict(coverage)


SUITE_LOADERS = {
    "ifeval": lambda args: load_ifeval_pairs(args.packs_root),
    "gpqa": lambda args: load_gpqa_pairs(args.packs_root, args.gpqa_csv),
    "livecodebench": lambda args: load_livecodebench_pairs(args.packs_root),
}
SUITE_STRATUM_FIELD = {
    "ifeval": "instruction_count",
    "gpqa": "High-level domain",
    "livecodebench": "difficulty|date_bucket",
}


# ---------------------------------------------------------------------------
# Historical exclusions.
# ---------------------------------------------------------------------------


def load_console_excluded(db_path: Path) -> dict[str, set[str]]:
    """`.local/console/console.sqlite3` runs.payload -> jobs[].episodes[].case_id.

    Note: these case ids are in the *pack's internal* case_id space, not
    necessarily the official id space (true for gpqa, whose pack case_id is a
    row index). Callers must translate via the suite's id_map before excluding.
    """
    if not db_path.exists():
        return {}
    result: dict[str, set[str]] = {}
    con = sqlite3.connect(str(db_path))
    try:
        for (payload,) in con.execute("select payload from runs"):
            try:
                data = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
            for job in data.get("jobs", []):
                bench = job.get("benchmark")
                if bench not in DEFAULT_SIZES:
                    continue
                for episode in job.get("episodes", []):
                    case_id = episode.get("case_id")
                    if case_id is not None:
                        result.setdefault(bench, set()).add(str(case_id))
    finally:
        con.close()
    return result


def load_inspect_log_excluded(logs_dir: Path) -> dict[str, set[str]]:
    """`.local/inspect-logs/*.eval` samples[].id, keyed by task name -> suite.

    These ids are already in official-id space (Inspect's own sample id).
    """
    if not logs_dir.exists():
        return {}
    result: dict[str, set[str]] = {}
    for path in sorted(logs_dir.glob("*.eval")):
        try:
            proc = subprocess.run(
                [
                    "uvx",
                    "--from",
                    f"inspect-ai=={INSPECT_VERSION}",
                    "inspect",
                    "log",
                    "dump",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            data = json.loads(proc.stdout)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            OSError,
        ):
            continue
        task = data.get("eval", {}).get("task", "")
        suite = LOG_TASK_TO_SUITE.get(task)
        if suite is None and "livecodebench" in task:
            suite = "livecodebench"
        if suite is None:
            continue
        for sample in data.get("samples", []):
            sample_id = sample.get("id")
            if sample_id is not None:
                result.setdefault(suite, set()).add(str(sample_id))
    return result


def resolve_excluded_ids(
    suite: str,
    pairs: list[tuple[str, str | None]],
    console_excluded: dict[str, set[str]],
    log_excluded: dict[str, set[str]],
    id_map: dict[str, str] | None = None,
) -> set[str]:
    """Combine console + inspect-log historical ids, normalized to official ids.

    `id_map` translates the pack's internal case_id -> official id, needed when
    they differ (gpqa). When absent, console case ids are assumed already-official
    (true for ifeval and livecodebench, whose pack case_id == official id).
    """
    excluded: set[str] = set()
    for case_id in console_excluded.get(suite, ()):
        if id_map is not None:
            official = id_map.get(case_id)
            if official is not None:
                excluded.add(official)
        else:
            excluded.add(case_id)
    excluded |= log_excluded.get(suite, set())
    return excluded


def build_id_map_for_gpqa(packs_root: Path, gpqa_csv: Path | None) -> dict[str, str]:
    """pack case_id (row index) -> official Record ID, for exclusion translation."""
    _manifest, rows = load_pack(packs_root, "gpqa")
    if rows and "Record ID" in rows[0]:
        return {row["case_id"]: str(row["Record ID"]) for row in rows}
    csv_path = gpqa_csv or (packs_root / "downloads" / "gpqa-diamond.csv")
    if not csv_path.exists():
        return {}
    csv_rows = _gpqa_source_csv_rows(csv_path)
    if len(csv_rows) != len(rows):
        return {}
    return {row["case_id"]: csv_rows[int(row["case_id"])]["Record ID"] for row in rows}


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def parse_size_override(value: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"([a-z]+)=(\d+),(\d+)", value)
    if not match:
        raise argparse.ArgumentTypeError("expected SUITE=CALIBRATION,MAIN, e.g. ifeval=10,120")
    suite, cal, main = match.groups()
    return suite, int(cal), int(main)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path(".local/inspect-samples"))
    parser.add_argument(
        "--suites", nargs="+", choices=list(DEFAULT_SUITES), default=list(DEFAULT_SUITES)
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing frozen output")
    parser.add_argument("--packs-root", type=Path, default=Path(".local/suites"))
    parser.add_argument("--console-db", type=Path, default=Path(".local/console/console.sqlite3"))
    parser.add_argument("--inspect-logs", type=Path, default=Path(".local/inspect-logs"))
    parser.add_argument("--gpqa-csv", type=Path, default=None)
    parser.add_argument(
        "--size",
        action="append",
        default=[],
        type=parse_size_override,
        metavar="SUITE=CALIBRATION,MAIN",
        help="override calibration/main sizes for a suite, e.g. --size gpqa=10,60",
    )
    return parser


def resolve_sizes(args: argparse.Namespace) -> dict[str, dict[str, int]]:
    sizes = {suite: dict(DEFAULT_SIZES[suite]) for suite in DEFAULT_SUITES}
    for suite, cal, main in args.size:
        if suite not in sizes:
            raise SystemExit(f"unknown suite in --size: {suite}")
        sizes[suite] = {"calibration": cal, "main": main}
    return sizes


def output_paths(out: Path, suite: str) -> list[Path]:
    return [out / f"{suite}.json", out / f"{suite}.calibration.txt", out / f"{suite}.main.txt"]


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    sizes = resolve_sizes(args)

    existing = [
        path for suite in args.suites for path in output_paths(args.out, suite) if path.exists()
    ]
    if existing and not args.force:
        print("refusing to overwrite existing frozen output (use --force):", file=sys.stderr)
        for path in existing:
            print(f"  {path}", file=sys.stderr)
        return 1

    console_excluded = load_console_excluded(args.console_db)
    log_excluded = load_inspect_log_excluded(args.inspect_logs)

    args.out.mkdir(parents=True, exist_ok=True)

    rows_for_table: list[tuple] = []
    had_failure = False
    for suite in args.suites:
        try:
            pairs, manifest, coverage = SUITE_LOADERS[suite](args)
        except SuiteUnavailable as exc:
            print(f"[{suite}] SKIPPED: {exc}", file=sys.stderr)
            had_failure = True
            continue

        id_map = build_id_map_for_gpqa(args.packs_root, args.gpqa_csv) if suite == "gpqa" else None
        excluded = resolve_excluded_ids(suite, pairs, console_excluded, log_excluded, id_map)

        result = freeze_suite(
            pairs,
            SUITE_STRATUM_FIELD[suite],
            sizes[suite]["calibration"],
            sizes[suite]["main"],
            args.seed,
            excluded,
        )
        result["suite"] = suite
        result["source_pack_sha256"] = manifest.get("sha256")
        result["instruction_or_domain_coverage"] = coverage

        json_path, cal_path, main_path = output_paths(args.out, suite)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        cal_path.write_text(",".join(result["calibration_ids"]))
        main_path.write_text(",".join(result["main_ids"]))

        rows_for_table.append(
            (
                suite,
                result["stratum_field"],
                result["population_after_exclusion"],
                result["excluded_ids_count"],
                len(result["calibration_ids"]),
                len(result["main_ids"]),
            )
        )

    if rows_for_table:
        header = ("suite", "stratum_field", "population", "excluded", "calibration", "main")
        widths = [
            max(len(str(header[i])), *(len(str(row[i])) for row in rows_for_table))
            for i in range(len(header))
        ]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*header))
        for row in rows_for_table:
            print(fmt.format(*row))

    return 1 if had_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
