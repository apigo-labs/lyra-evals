from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx

SHA256 = re.compile(r"^[a-f0-9]{64}$")


class DatasetIntegrityError(RuntimeError):
    """A dataset artifact violates the frozen manifest."""


@dataclass(frozen=True)
class MaterializedArtifact:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class DatasetSnapshot:
    name: str
    revision: str
    artifacts: tuple[MaterializedArtifact, ...]
    snapshot_sha256: str
    record_count: int | None = None
    ids_sha256: str | None = None


class DatasetCache:
    """Downloads and verifies frozen public artifacts behind one small interface."""

    def __init__(
        self,
        root: Path,
        *,
        client: httpx.Client | None = None,
        max_artifact_bytes: int = 1_000_000_000,
    ) -> None:
        self.root = root
        self.client = client or httpx.Client(follow_redirects=True, timeout=120)
        self.max_artifact_bytes = max_artifact_bytes

    def plan(self, benchmark_manifest: dict[str, Any]) -> DatasetSnapshot:
        name, revision, dataset, artifacts = self._validated_specs(benchmark_manifest)
        materialized: list[MaterializedArtifact] = []
        for artifact in artifacts:
            target = self.root / name / revision / artifact["path"]
            size = 0
            if target.exists():
                digest, size = _digest_file(target)
                if digest != artifact["sha256"]:
                    raise DatasetIntegrityError(f"cached artifact hash mismatch: {target}")
            materialized.append(
                MaterializedArtifact(
                    path=target,
                    sha256=artifact["sha256"],
                    size_bytes=size,
                )
            )
        record_count, ids_sha256 = self._verify_records(dataset, materialized, required=False)
        return self._snapshot(name, revision, materialized, record_count, ids_sha256)

    def materialize(self, benchmark_manifest: dict[str, Any]) -> DatasetSnapshot:
        name, revision, dataset, artifacts = self._validated_specs(benchmark_manifest)
        materialized: list[MaterializedArtifact] = []
        for artifact in artifacts:
            target = self.root / name / revision / artifact["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                digest, size = _digest_file(target)
                if digest != artifact["sha256"]:
                    raise DatasetIntegrityError(f"cached artifact hash mismatch: {target}")
            else:
                digest, size = self._download(artifact["url"], target)
                if digest != artifact["sha256"]:
                    target.unlink(missing_ok=True)
                    raise DatasetIntegrityError(f"downloaded artifact hash mismatch: {target}")
            materialized.append(MaterializedArtifact(path=target, sha256=digest, size_bytes=size))
        record_count, ids_sha256 = self._verify_records(dataset, materialized, required=True)
        return self._snapshot(name, revision, materialized, record_count, ids_sha256)

    def _download(self, url: str, target: Path) -> tuple[str, int]:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(descriptor)
        temporary_path = Path(temporary)
        digest = hashlib.sha256()
        size = 0
        try:
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                with temporary_path.open("wb") as output:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.max_artifact_bytes:
                            raise DatasetIntegrityError(f"artifact exceeds size limit: {url}")
                        digest.update(chunk)
                        output.write(chunk)
            temporary_path.replace(target)
            return digest.hexdigest(), size
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validated_specs(
        benchmark_manifest: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any], list[dict[str, str]]]:
        name = str(benchmark_manifest.get("name", "")).strip()
        dataset = benchmark_manifest.get("dataset", {})
        revision = str(dataset.get("revision", "")).strip()
        artifacts = dataset.get("artifacts", [])
        if not name or revision in {"", "UNPINNED"}:
            raise DatasetIntegrityError("dataset name and immutable revision are required")
        if not isinstance(artifacts, list) or not artifacts:
            raise DatasetIntegrityError("at least one frozen dataset artifact is required")
        checked: list[dict[str, str]] = []
        for raw in artifacts:
            url = str(raw.get("url", ""))
            relative = PurePosixPath(str(raw.get("path", "")))
            digest = str(raw.get("sha256", ""))
            if urlparse(url).scheme != "https":
                raise DatasetIntegrityError(f"dataset URL must use HTTPS: {url}")
            if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
                raise DatasetIntegrityError(f"unsafe dataset artifact path: {relative}")
            if not SHA256.fullmatch(digest):
                raise DatasetIntegrityError(f"invalid artifact SHA-256: {digest}")
            checked.append({"url": url, "path": str(relative), "sha256": digest})
        return name, revision, dataset, checked

    @staticmethod
    def _verify_records(
        dataset: dict[str, Any],
        artifacts: list[MaterializedArtifact],
        *,
        required: bool,
    ) -> tuple[int | None, str | None]:
        data_format = dataset.get("format")
        if data_format not in {"jsonl", "csv", "tsv"}:
            return None, None
        expected_count = dataset.get("expected_count")
        id_field = str(dataset.get("id_field", "")).strip()
        expected_ids_sha256 = str(dataset.get("ids_sha256", "")).strip()
        if not isinstance(expected_count, int) or expected_count <= 0:
            raise DatasetIntegrityError("JSONL dataset requires a positive expected_count")
        if data_format == "jsonl" and not id_field:
            raise DatasetIntegrityError("JSONL dataset requires id_field")
        if not SHA256.fullmatch(expected_ids_sha256):
            raise DatasetIntegrityError(f"{data_format.upper()} dataset requires ids_sha256")
        if len(artifacts) != 1:
            raise DatasetIntegrityError("verified JSONL datasets require exactly one artifact")
        path = artifacts[0].path
        if not path.exists():
            if required:
                raise DatasetIntegrityError(f"dataset artifact is missing: {path}")
            return None, None
        ids: list[str] = []
        if data_format == "jsonl":
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    identifier = str(row[id_field])
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise DatasetIntegrityError(
                        f"invalid JSONL record at {path}:{line_number}"
                    ) from exc
                ids.append(identifier)
        else:
            try:
                with path.open(encoding="utf-8", newline="") as source:
                    rows = list(
                        csv.DictReader(source, delimiter="\t" if data_format == "tsv" else ",")
                    )
            except (csv.Error, UnicodeDecodeError) as exc:
                raise DatasetIntegrityError(
                    f"invalid {data_format.upper()} dataset: {path}"
                ) from exc
            required_fields = set(dataset.get("required_fields", []))
            if not rows or not required_fields.issubset(rows[0]):
                raise DatasetIntegrityError(
                    f"{data_format.upper()} dataset is missing required fields: {path}"
                )
            ids = [str(index) for index in range(len(rows))]
        if len(ids) != expected_count:
            raise DatasetIntegrityError(
                f"record count mismatch for {path}: expected {expected_count}, got {len(ids)}"
            )
        if len(set(ids)) != len(ids):
            raise DatasetIntegrityError(f"duplicate dataset IDs in {path}")
        digest = hashlib.sha256()
        for identifier in ids:
            digest.update(identifier.encode())
            digest.update(b"\n")
        actual_ids_sha256 = digest.hexdigest()
        if actual_ids_sha256 != expected_ids_sha256:
            raise DatasetIntegrityError(
                f"dataset ID manifest mismatch for {path}: {actual_ids_sha256}"
            )
        return len(ids), actual_ids_sha256

    @staticmethod
    def _snapshot(
        name: str,
        revision: str,
        artifacts: list[MaterializedArtifact],
        record_count: int | None,
        ids_sha256: str | None,
    ) -> DatasetSnapshot:
        combined = hashlib.sha256()
        for artifact in sorted(artifacts, key=lambda item: str(item.path)):
            combined.update(str(artifact.path.name).encode())
            combined.update(artifact.sha256.encode())
        return DatasetSnapshot(
            name=name,
            revision=revision,
            artifacts=tuple(artifacts),
            snapshot_sha256=combined.hexdigest(),
            record_count=record_count,
            ids_sha256=ids_sha256,
        )


def _digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
