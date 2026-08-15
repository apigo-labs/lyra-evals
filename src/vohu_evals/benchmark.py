from __future__ import annotations

import importlib.util
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml

from vohu_evals.judge import JudgePort
from vohu_evals.models import BenchmarkRequest, Case, CaseScore, InvocationResult, RunStage


class DatasetNotReadyError(RuntimeError):
    """Raised when a non-fixture stage has no frozen public dataset."""


class Benchmark(ABC):
    """The small interface each benchmark plugin exposes to the runner."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = yaml.safe_load((root / "benchmark.yaml").read_text(encoding="utf-8"))

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    def fetch(self) -> None:
        if self.manifest["dataset"]["revision"] == "UNPINNED":
            raise DatasetNotReadyError(f"{self.name} dataset revision is not frozen")

    def validate(self, stage: RunStage) -> None:
        required = {"name", "protocol_version", "implementation_status", "dataset", "metric"}
        missing = required.difference(self.manifest)
        if missing:
            raise ValueError(f"{self.name} manifest missing fields: {sorted(missing)}")
        if stage is RunStage.PUBLICATION and self.manifest["implementation_status"] != "verified":
            raise DatasetNotReadyError(f"{self.name} is not publication-ready")

    def enumerate_cases(self, stage: RunStage) -> list[Case]:
        self.validate(stage)
        if stage is not RunStage.FIXTURE:
            raise DatasetNotReadyError(f"{self.name} only has synthetic fixtures in the scaffold")
        fixture_path = self.root / "fixtures" / "cases.jsonl"
        cases: list[Case] = []
        for line in fixture_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            cases.append(
                Case(
                    case_id=raw["case_id"],
                    prompt=raw["prompt"],
                    expected=raw["expected"],
                    metadata=raw.get("metadata", {}),
                    fixture_response=raw.get("fixture_response"),
                )
            )
        return cases

    def build_request(self, case: Case) -> BenchmarkRequest:
        return BenchmarkRequest(
            case_id=case.case_id,
            prompt=case.prompt,
            web_search=bool(self.manifest.get("capabilities", {}).get("web_search", False)),
        )

    def parse_response(self, result: InvocationResult) -> str:
        return result.output_text.strip()

    def bind_judge(self, judge: JudgePort | None) -> None:
        """Allows benchmarks with an LLM evaluator to receive a Gateway-backed judge."""
        return None

    def evaluator_requests_per_case(self, case: Case) -> int:
        """Returns the frozen evaluator-call budget for one case."""
        return 0

    def evaluator_requests_for_response(self, case: Case, parsed_output: str) -> int:
        """Returns evaluator calls needed to rescore an existing target response."""
        return self.evaluator_requests_per_case(case)

    @abstractmethod
    def score_case(self, case: Case, parsed_output: str, result: InvocationResult) -> CaseScore:
        raise NotImplementedError

    def aggregate(self, scores: list[CaseScore]) -> dict[str, Any]:
        return {
            "metric": self.manifest["metric"],
            "score": sum(score.value for score in scores) / len(scores) if scores else 0.0,
            "cases": len(scores),
        }


def load_benchmark(project_root: Path, name: str) -> Benchmark:
    plugin_path = project_root / "benchmarks" / name / "plugin.py"
    if not plugin_path.is_file():
        raise ValueError(f"unknown benchmark: {name}")
    spec = importlib.util.spec_from_file_location(f"vohu_eval_plugin_{name}", plugin_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load benchmark plugin: {plugin_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = module.Plugin(plugin_path.parent)
    if not isinstance(plugin, Benchmark):
        raise TypeError(f"{plugin_path} Plugin must extend Benchmark")
    return plugin
