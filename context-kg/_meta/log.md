---
title: VOHU Evals Context-KG 变更日志
tags: [meta, log]
links: []
updated: 2026-08-24
sources: 0
---

# VOHU Evals Context-KG 变更日志

## [2026-08-24] change | 题目级并发与可恢复请求预算

- Runner 支持冻结的题目级并发度，默认串行且不改变单题 VOHU 编排。
- SQLite 在真实调用前原子预留 attempt 和全局请求额度，保证并发上限与断点续跑语义。
- 并发度进入不可变 run manifest 和标识，响应解析与评分保持串行。

## [2026-08-15] govern | Targets 本地化

- `targets/` 调整为被 Git 忽略的本地运行输入，不再提交真实 allowlist 或 composition。
- 默认离线验证不依赖该目录；执行评测时仍要求本地 target 完整并保持 fail closed。

## [2026-08-15] restructure | 聚焦评测实现并完成脱敏

- 删除历史实验流水、外部模型宣传研究、账号操作、部署状态与跨仓库运维信息。
- 将长期知识重组为运行架构、Benchmark 接入、评分证据和测试门禁四个实现页面。
- 具体数据源、版本与校验值回归代码 manifest 管理，知识库不再复制运行态值。
- 重写任务与经验页面，新增禁止记录敏感和运行流水信息的长期边界。

## 相关页面
