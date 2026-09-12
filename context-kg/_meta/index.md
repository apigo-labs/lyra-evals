---
title: VOHU Evals Context-KG 索引
tags: [meta, index]
links: [vohu-evals-architecture, benchmark-integration, scoring-evidence, vohu-evals-test-strategy]
updated: 2026-09-09
sources: 0
---

# VOHU Evals Context-KG 索引

## Technical

- [[concurrency-and-quota-design]] — 做题/请求/评分分层并发、APIGO 配额与自适应调度（设计提案） | concurrency, quota

- [[model-effort-evaluation-design]] — 模型/Harness/effort 矩阵、预算公平性、Console 计划与验收（待实现） | evaluation, proposal

- [[local-console-execution-design]] — 本地 Console、Paper/Ink 主题、Docker/ACP 并行调度与交付边界 | console, execution
- [[fusion-router-evaluation-design]] — Fusion Router 与 APIGO 单模型的横向评测设计提案（未实现） | proposal, evaluation
- [[vohu-evals-architecture]] — VOHU 专用评测运行链路与模块边界 | architecture, evaluation, vohu

## Quality

- [[fusion-roadshow-protocol]] — 三赛道路演提案状态与比较边界 | proposal, evaluation

- [[ifeval-statistical-reporting]] — 四项指标、评分覆盖率、区间与逐题执行证据 | ifeval, statistics

- [[benchmark-onboarding-checklist]] — 当前六个插件、候选接入项与 Fusion 无内置搜索约束 | benchmark, integration
- [[benchmark-integration]] — Benchmark 插件、数据冻结与就绪门禁 | quality, benchmark, dataset
- [[scoring-evidence]] — 固定分母评分、账本、派生重评与报告证据 | quality, scoring, evidence
- [[vohu-evals-test-strategy]] — 离线测试、契约验证与发布门禁 | quality, testing, verification

## Tasks

- [[todo]] — 当前任务计划与 Review | tasks, vohu
- [[lessons]] — 实现与文档边界经验 | tasks, lessons

## 相关页面

- [[vohu-evals-architecture]]
- [[benchmark-integration]]
- [[scoring-evidence]]
- [[vohu-evals-test-strategy]]
