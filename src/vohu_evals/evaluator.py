from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from vohu_evals.dataset import DatasetCache, DatasetSnapshot


class EvaluatorResourceError(RuntimeError):
    """A frozen evaluator resource cannot be safely materialized."""


class EvaluatorResourceCache:
    """Owns non-code resources required by a frozen official evaluator."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.cache_root = project_root / ".cache" / "evaluators"

    def plan(self, manifest: dict[str, Any]) -> DatasetSnapshot:
        return DatasetCache(self.cache_root).plan(self._resource_manifest(manifest))

    def materialize(self, manifest: dict[str, Any]) -> DatasetSnapshot:
        snapshot = DatasetCache(self.cache_root).materialize(self._resource_manifest(manifest))
        tokenizers = self.nltk_data_path(manifest) / "tokenizers"
        tokenizers.mkdir(parents=True, exist_ok=True)
        for artifact in snapshot.artifacts:
            self._extract_zip(artifact.path, tokenizers)
        return snapshot

    def nltk_data_path(self, manifest: dict[str, Any]) -> Path:
        evaluator = manifest["official_evaluator"]
        revision = evaluator["resources"]["revision"]
        return self.cache_root / f"{manifest['name']}-resources" / revision / "nltk_data"

    @staticmethod
    def _resource_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        resources = manifest.get("official_evaluator", {}).get("resources")
        if not isinstance(resources, dict):
            raise EvaluatorResourceError("official evaluator resources are not frozen")
        return {"name": f"{manifest['name']}-resources", "dataset": resources}

    @staticmethod
    def _extract_zip(archive: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise EvaluatorResourceError(f"unsafe evaluator archive member: {relative}")
                if member.is_dir():
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as input_file, target.open("wb") as output_file:
                    while chunk := input_file.read(1024 * 1024):
                        output_file.write(chunk)
