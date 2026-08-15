---
title: VOHU Evals 评分与证据
tags: [quality, scoring, evidence]
links: [vohu-evals-architecture, benchmark-integration, vohu-evals-test-strategy]
updated: 2026-08-15
sources: 6
---

# VOHU Evals 评分与证据

## 固定分母

Run 的总题数由冻结 case 集决定。总分以全部 case 为分母；系统失败与无效输出计零，不能从分母中移除。
只有所有 case 都进入终态时，run 才能标记完成。

## 单题评分

- 确定性规则优先于 LLM judge，例如严格相等或官方规则可直接判定时不调用 judge。
- 必须调用 judge 的赛道由 plugin 冻结 prompt、协议和调用次数，并将 evaluator 用量写入评分详情。
- 解析 verdict 时使用明确的最终字段或行锚定规则，避免把推理文本误判为最终结论。
- 聚合逻辑属于赛道 plugin；Runner 不假设所有指标都是普通 accuracy。

## 账本与重评

SQLite ledger 是本地运行事实源，保存不可变 manifest、case 状态、分数、响应、审计标识、attempts、usage、成本和
重试次数。派生重评创建新的 run，不修改原 run，也不再次调用被测 VOHU；派生关系进入 manifest 和 evidence。

## Evidence 与发布

- Evidence 从 ledger 确定性生成，包含指标、可靠性、协议、请求计数与派生信息。
- Publication 只在 publication 阶段、固定分母完全终态且没有系统失败或无效输出时开放。
- Report verifier 要求每个数字或比较声明能映射到 evidence；Codex 只负责文字组织，不负责重算。
- 外部参考若存在，必须独立标记可比性，不能改变 VOHU 原始指标或发布门禁。

## 证据

- `src/vohu_evals/models.py`
- `src/vohu_evals/ledger.py`
- `src/vohu_evals/judge.py`
- `src/vohu_evals/rescoring.py`
- `src/vohu_evals/reporting.py`
- `src/vohu_evals/runner.py`

## 相关页面

- [[vohu-evals-architecture]]
- [[benchmark-integration]]
- [[vohu-evals-test-strategy]]
