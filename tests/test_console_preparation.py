import asyncio
import json
from pathlib import Path

from vohu_evals.console.preparation import Preparations
from vohu_evals.suites.packs import freeze_pack


def test_swe_preparation_selects_frozen_cases_and_never_starts_model(tmp_path, monkeypatch):
    from vohu_evals.console import preparation

    frozen = freeze_pack(
        tmp_path / ".local/suites",
        "swebench",
        [{"case_id": "one"}, {"case_id": "two"}],
        {"revision": "fixture"},
    )
    monkeypatch.setattr(preparation, "ROOT", tmp_path)
    calls = []

    async def command(*args, **kwargs):
        calls.append(args)
        assert json.loads(Path(args[-1]).read_text()) == [{"case_id": "two"}]

    async def preflight(rows, root):
        assert rows == [{"case_id": "two"}]

    monkeypatch.setattr(preparation, "command", command)
    monkeypatch.setattr(preparation, "preflight", preflight)
    plan = {
        "id": "fixture",
        "manifest": {"datasets": {"swebench": {"sha256": frozen["sha256"], "case_ids": ["two"]}}},
    }

    async def run():
        manager = Preparations()
        assert manager.start(plan)["status"] == "preparing"
        manager.start(plan)
        await asyncio.gather(*list(manager.tasks.values()))
        assert manager.states["fixture"]["status"] == "ready"
        await manager.close()

    asyncio.run(run())
    assert len(calls) == 1 and "--prepare" in calls[0]
