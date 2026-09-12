"""Explicit, zero-inference preparation of selected SWE environments."""

import asyncio
import json
from pathlib import Path

from vohu_evals.suites.packs import dataset_root, load_pack
from vohu_evals.suites.swe_live import command, preflight

ROOT = Path(__file__).resolve().parents[3]


class Preparations:
    def __init__(self):
        self.states = {}
        self.tasks = {}
        self.lock = asyncio.Lock()

    def start(self, plan):
        identifier = plan["id"]
        if identifier in self.tasks:
            return self.states[identifier]
        self.states[identifier] = {
            "status": "preparing",
            "message": "正在准备所选官方镜像，不调用模型",
        }
        self.tasks[identifier] = asyncio.create_task(self.work(plan))
        return self.states[identifier]

    async def work(self, plan):
        identifier = plan["id"]
        try:
            async with self.lock:
                manifest, rows = load_pack(
                    dataset_root(
                        ROOT / ".local/suites",
                        "swebench",
                        plan["manifest"].get("swe_subset", "verified"),
                    ),
                    "swebench",
                )
                frozen = plan["manifest"]["datasets"]["swebench"]
                if manifest["sha256"] != frozen["sha256"]:
                    raise ValueError("Dataset changed")
                selected = [r for r in rows if r["case_id"] in frozen["case_ids"]]
                directory = ROOT / ".local/preparation" / identifier
                directory.mkdir(parents=True, exist_ok=True)
                dataset = directory / "dataset.json"
                dataset.write_text(json.dumps(selected))
                await command(
                    str(ROOT / ".local/benchmark-runtime/bin/python"),
                    str(ROOT / "scripts/swe_runtime.py"),
                    "--cache",
                    str(ROOT / ".local/swe-specs"),
                    "--prepare",
                    str(dataset),
                    cwd=ROOT,
                    timeout=3600,
                )
                await preflight(selected, ROOT)
                self.states[identifier] = {
                    "status": "ready",
                    "message": "所选实例环境已冻结，可以启动验收",
                }
        except (ValueError, OSError, RuntimeError, TimeoutError):
            self.states[identifier] = {
                "status": "failed",
                "message": "准备失败，请检查 Docker、官方镜像网络或先运行 make benchmark-runtimes",
            }
        finally:
            self.tasks.pop(identifier, None)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
