from __future__ import annotations

import json
from pathlib import Path

from vohu_evals.suites.packs import dataset_root, load_pack

DETAILS = {
    "ifeval": "官方 strict/loose 评分器与真实 ACP 调度已接入；价格上界估算可用，账单待对账",
    "gpqa": "作者公开 Diamond、答案解析与真实 ACP 调度已接入；账单待对账",
    "livecodebench": "release_v6 lite；真实 ACP 生成、官方断网 Docker 评分已接入；账单待对账",
    "tau2": (
        "三个领域 base；官方 Gym + MCP 工具桥已接入；"
        "需准备 runtime 与固定模拟器，预算包含模拟器/Judge"
    ),
    "swebench": "Verified；已接入独立工作区、ACP 修改、patch 收集与官方 evaluator；需准备实例环境",
}


def suite_status(root: Path, items: list[dict]) -> list[dict]:
    result = []
    for item in items:
        suite = item["id"]
        manifest_path = root / suite / "manifest.json"
        manifest = None
        error = None
        if manifest_path.is_file():
            try:
                manifest, _ = load_pack(root, suite)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                error = "数据完整性检查失败"
        data_ready = manifest is not None
        reason = DETAILS[suite]
        if suite == "swebench":
            try:
                lite, _ = load_pack(dataset_root(root, suite, "lite"), suite)
                reason += f"；Lite {lite['count']} 题已冻结，可在新建计划中选择"
            except (OSError, ValueError, KeyError, TypeError):
                reason += "；Lite 数据尚未准备"
        if not data_ready:
            reason += "；" + (error or "数据尚未准备")
        result.append(
            {
                **item,
                "status": "适配已接入 · 数据就绪" if data_ready else "适配已接入 · 缺数据",
                "cases": manifest["count"] if manifest else item["cases"],
                "data_ready": data_ready,
                "data_sha256": manifest["sha256"] if manifest else None,
                "live_ready": False,
                "adapter_ready": True,
                "reason": reason,
            }
        )
    return result
