"""Explicit official data preparation. No model calls. Raw data stays in .local."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from urllib.request import urlopen

from vohu_evals.benchmark import load_benchmark
from vohu_evals.dataset import DatasetCache
from vohu_evals.evaluator import EvaluatorResourceCache
from vohu_evals.suites.packs import freeze_pack, gpqa_rows

ROOT = Path(__file__).resolve().parents[1]
LCB_REV = "0fe84c3912ea0c4d4a78037083943e8f0c4dd505"
SWE_REV = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
TAU_REV = "672227c6b6676edc20d57ea53b7000262aae77b9"


def download(url: str, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_suffix(".partial")
        try:
            with urlopen(url, timeout=120) as response, temporary.open("wb") as stream:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > 2 * 1024 * 1024 * 1024:
                        raise ValueError("Dataset exceeds download limit")
                    stream.write(chunk)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    with destination.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(suite: str, gpqa_csv: Path | None = None):
    packs = ROOT / ".local/suites"
    if suite == "ifeval":
        benchmark = load_benchmark(ROOT, suite)
        snapshot = DatasetCache(ROOT / ".cache/datasets").materialize(benchmark.manifest)
        EvaluatorResourceCache(ROOT).materialize(benchmark.manifest)
        rows = [
            {**json.loads(line), "case_id": str(json.loads(line)["key"])}
            for line in snapshot.artifacts[0].path.read_text().splitlines()
            if line
        ]
        return freeze_pack(
            packs,
            suite,
            rows,
            {"revision": snapshot.revision, "sha256": snapshot.artifacts[0].sha256},
        )
    if suite == "gpqa":
        source = {"access": "user-provided-authorized"}
        if gpqa_csv is None:
            revision = "56686c06f5e19865c153de0fdb11be3890014df7"
            url = f"https://raw.githubusercontent.com/idavidrein/gpqa/{revision}/dataset.zip"
            archive = packs / "downloads/gpqa-official.zip"
            checksum = download(url, archive)
            with zipfile.ZipFile(archive) as zipped:
                # Public extraction password documented by the dataset authors in README.
                content = zipped.read("dataset/gpqa_diamond.csv", pwd=b"deserted-untie-orchid")
            gpqa_csv = packs / "downloads/gpqa-diamond.csv"
            gpqa_csv.write_bytes(content)
            source = {
                "url": url,
                "revision": revision,
                "archive_sha256": checksum,
                "access": "author-public-release",
            }
        rows = gpqa_rows(gpqa_csv)
        if len(rows) != 198:
            raise ValueError("GPQA Diamond requires 198 rows")
        return freeze_pack(
            packs,
            suite,
            rows,
            {
                "sha256": hashlib.sha256(gpqa_csv.read_bytes()).hexdigest(),
                "shuffle_seed": 0,
                "subset": "diamond",
                **source,
            },
        )
    if suite == "livecodebench":
        rows = []
        artifacts = {}
        for filename in [
            "test.jsonl",
            "test2.jsonl",
            "test3.jsonl",
            "test4.jsonl",
            "test5.jsonl",
            "test6.jsonl",
        ]:
            url = f"https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/{LCB_REV}/{filename}"
            path = packs / "downloads" / f"lcb-{LCB_REV}-{filename}"
            artifacts[filename] = download(url, path)
            expected = json.loads(
                (ROOT / "deploy/suites/livecodebench/source-files.json").read_text()
            )[filename]
            if artifacts[filename] != expected["sha256"]:
                raise ValueError("LCB official source checksum mismatch")
            with path.open() as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    raw = json.loads(line)
                    raw_bytes = line.encode()
                    case_hash = hashlib.sha256(raw_bytes).hexdigest()
                    case_dir = packs / "livecodebench" / "artifacts"
                    case_dir.mkdir(parents=True, exist_ok=True)
                    (case_dir / f"{case_hash}.json").write_bytes(raw_bytes)
                    rows.append(
                        {
                            "case_id": str(raw["question_id"]),
                            "question_content": raw["question_content"],
                            "starter_code": raw.get("starter_code", ""),
                            "public_test_cases": raw["public_test_cases"],
                            "grader_sha256": case_hash,
                        }
                    )
        if len(rows) != 1055:
            raise ValueError("LiveCodeBench release_v6 requires 1055 rows")
        return freeze_pack(
            packs,
            suite,
            rows,
            {
                "revision": LCB_REV,
                "artifacts": artifacts,
                "release": "release_v6",
                "subset": "lite",
            },
        )
    if suite == "swebench":
        import pyarrow.parquet as parquet

        url = f"https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/resolve/{SWE_REV}/data/test-00000-of-00001.parquet"
        path = packs / "downloads" / f"swe-{SWE_REV}.parquet"
        checksum = download(url, path)
        rows = [
            {**row, "case_id": row["instance_id"]} for row in parquet.read_table(path).to_pylist()
        ]
        if len(rows) != 500:
            raise ValueError("SWE Verified requires 500 rows")
        return freeze_pack(
            packs,
            suite,
            rows,
            {"url": url, "revision": SWE_REV, "sha256": checksum, "subset": "verified"},
        )
    if suite == "tau2":
        upstream = ROOT / ".local/upstream/tau2"
        revision = subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
        ).strip()
        if revision != TAU_REV:
            raise ValueError("tau2 upstream revision mismatch")
        rows = []
        artifacts = {}
        for domain in ["retail", "airline", "telecom"]:
            path = upstream / "data/tau2/domains" / domain / "tasks.json"
            artifacts[domain] = hashlib.sha256(path.read_bytes()).hexdigest()
            split_path = path.parent / "split_tasks.json"
            selected = set(json.loads(split_path.read_text())["base"])
            artifacts[domain + "_split"] = hashlib.sha256(split_path.read_bytes()).hexdigest()
            selected_rows = [row for row in json.loads(path.read_text()) if row["id"] in selected]
            if len(selected_rows) != len(selected):
                raise ValueError("tau2 split/task mismatch")
            rows.extend(
                {
                    "case_id": f"{domain}:{row['id']}",
                    "domain": domain,
                    "task_id": row["id"],
                    "private_task": row,
                }
                for row in selected_rows
            )
        return freeze_pack(
            packs,
            suite,
            rows,
            {
                "revision": revision,
                "artifacts": artifacts,
                "domains": ["retail", "airline", "telecom"],
                "split": "base",
            },
        )
    raise ValueError("Unknown suite")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "suites", nargs="+", choices=["ifeval", "gpqa", "livecodebench", "tau2", "swebench"]
    )
    parser.add_argument("--gpqa-csv", type=Path)
    args = parser.parse_args()
    for suite in args.suites:
        try:
            print(json.dumps(prepare(suite, args.gpqa_csv), ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"suite": suite, "error": str(exc)}, ensure_ascii=False))
            raise


if __name__ == "__main__":
    main()
