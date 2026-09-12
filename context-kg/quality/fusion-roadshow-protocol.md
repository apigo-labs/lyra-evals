---
title: Fusion 路演实验设计状态
tags: [quality, evaluation, proposal]
links: [ifeval-statistical-reporting]
updated: 2026-09-09
sources: 0
---

# Fusion 路演实验设计状态

已形成 IFEval、GPQA、LiveCodeBench 三赛道公开子集实验提案，具体模型矩阵和运行配置保留在本地设计材料中，不作为已执行报告发布。

现有代码提供三赛道 ACP 执行；提案中的 Inspect AI direct-api profile 尚未接入。后续实现必须显式区分 direct-api 与 ACP profile，不能混合归因。原生 effort、工具与输出约束、评分与成本证据共同决定比较口径。

提案要求分离校准题与主测试题，预注册主要对照和质量界限，按题配对分析；不把未显著不同解读为等效。主测试不得用来修改路由策略或选择有利题目。公开子集结果仅能描述该任务分布，不能替代客户流量证据。

待实现项包括分层抽样元数据、平衡区块调度、配对差异分析与直接端点适配；费用对账缺失时继续保持估算与实际账单分离。提案不授权付费运行。

## 相关页面

- [[ifeval-statistical-reporting]]
