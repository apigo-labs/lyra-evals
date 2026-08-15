from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from vohu_evals.dataset import DatasetCache, DatasetIntegrityError


def _manifest(content: bytes) -> dict:
    return {
        "name": "fixture",
        "dataset": {
            "revision": "commit-123",
            "artifacts": [
                {
                    "url": "https://official.example/data.jsonl",
                    "path": "data.jsonl",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        },
    }


def test_materializes_and_reuses_verified_artifact(tmp_path: Path) -> None:
    content = b'{"case_id":"one"}\n'
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=content, request=request)

    cache = DatasetCache(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    first = cache.materialize(_manifest(content))
    second = cache.materialize(_manifest(content))
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.artifacts[0].path.read_bytes() == content
    assert calls == 1


def test_rejects_mismatched_download(tmp_path: Path) -> None:
    request = httpx.Request("GET", "https://official.example/data.jsonl")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"bad", request=request)
        )
    )
    with pytest.raises(DatasetIntegrityError, match="hash mismatch"):
        DatasetCache(tmp_path, client=client).materialize(_manifest(b"expected"))
    assert not (tmp_path / "fixture" / "commit-123" / "data.jsonl").exists()


def test_verifies_jsonl_count_and_ordered_id_manifest(tmp_path: Path) -> None:
    content = b'{"key":1000}\n{"key":1001}\n'
    ids = hashlib.sha256(b"1000\n1001\n").hexdigest()
    manifest = _manifest(content)
    manifest["dataset"].update(
        {"format": "jsonl", "expected_count": 2, "id_field": "key", "ids_sha256": ids}
    )
    request = httpx.Request("GET", "https://official.example/data.jsonl")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=content, request=request)
        )
    )

    snapshot = DatasetCache(tmp_path, client=client).materialize(manifest)

    assert snapshot.record_count == 2
    assert snapshot.ids_sha256 == ids


def test_verifies_tsv_count_fields_and_ordered_id_manifest(tmp_path: Path) -> None:
    content = b"Prompt\tAnswer\nquestion one\tanswer one\nquestion two\tanswer two\n"
    ids = hashlib.sha256(b"0\n1\n").hexdigest()
    manifest = _manifest(content)
    manifest["dataset"].update(
        {
            "format": "tsv",
            "expected_count": 2,
            "ids_sha256": ids,
            "required_fields": ["Prompt", "Answer"],
        }
    )
    request = httpx.Request("GET", "https://official.example/data.tsv")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=content, request=request)
        )
    )

    snapshot = DatasetCache(tmp_path, client=client).materialize(manifest)

    assert snapshot.record_count == 2
    assert snapshot.ids_sha256 == ids


def test_rejects_jsonl_id_manifest_mismatch(tmp_path: Path) -> None:
    content = b'{"key":1000}\n'
    manifest = _manifest(content)
    manifest["dataset"].update(
        {
            "format": "jsonl",
            "expected_count": 1,
            "id_field": "key",
            "ids_sha256": "a" * 64,
        }
    )
    request = httpx.Request("GET", "https://official.example/data.jsonl")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=content, request=request)
        )
    )

    with pytest.raises(DatasetIntegrityError, match="ID manifest mismatch"):
        DatasetCache(tmp_path, client=client).materialize(manifest)


@pytest.mark.parametrize(
    "url,path,digest",
    [
        ("http://official.example/data", "data", "a" * 64),
        ("https://official.example/data", "../data", "a" * 64),
        ("https://official.example/data", "data", "not-a-hash"),
    ],
)
def test_rejects_unsafe_artifact_specs(tmp_path: Path, url: str, path: str, digest: str) -> None:
    manifest = {
        "name": "fixture",
        "dataset": {
            "revision": "commit-123",
            "artifacts": [{"url": url, "path": path, "sha256": digest}],
        },
    }
    with pytest.raises(DatasetIntegrityError):
        DatasetCache(tmp_path).plan(manifest)
