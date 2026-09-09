---
title: VOHU Evals Benchmark 接入
tags: [quality, benchmark, dataset]
links: [vohu-evals-architecture, scoring-evidence, vohu-evals-test-strategy]
updated: 2026-08-15
sources: 10
---

# VOHU Evals Benchmark 接入

## 插件契约

每个赛道位于独立目录，由 `benchmark.yaml` 和 `plugin.py` 组成。插件继承 `Benchmark`，负责：

- 校验实现状态与运行阶段；
- 枚举冻结 case；
- 从 case 构造 VOHU 请求；
- 解析目标输出；
- 对单题评分并聚合赛道指标；
- 在需要时声明并使用版本化 evaluator。

Runner 只依赖这一小接口，不理解具体题型、答案格式或 grader 细节。

## 数据冻结

- 数据集必须声明不可变 revision、HTTPS artifact、相对缓存路径和 SHA-256。
- 结构化数据还要校验记录数、必需字段、重复 ID 和有序 ID manifest。
- 下载先写临时文件，校验成功后原子替换；失败 artifact 不进入缓存。
- 题库只物化到 Git 忽略的缓存目录，fixtures 仅使用合成内容。
- 需要额外资源的官方 evaluator 使用独立缓存，并拒绝不安全的归档路径。

## 就绪门禁

Publication 阶段要求插件已验证、数据和 evaluator 已冻结、缓存完整，且 comparability 不处于实验或不兼容状态。
任何 blocker 都应作为机器可读 issue 返回，而不是由运行者口头忽略。

## 赛道隔离

仓库中的赛道分别维护 manifest、plugin、fixtures 和指标。不同赛道不得合成一个总分；开发划分、联网能力、
LLM judge 和多次判分等差异必须进入各自协议版本与 evidence。

## 证据

- `src/vohu_evals/benchmark.py`
- `src/vohu_evals/dataset.py`
- `src/vohu_evals/evaluator.py`
- `src/vohu_evals/readiness.py`
- `benchmarks/browsecomp/plugin.py`
- `benchmarks/draco/plugin.py`
- `benchmarks/frames/plugin.py`
- `benchmarks/gpqa/plugin.py`
- `benchmarks/hle/plugin.py`
- `benchmarks/ifeval/plugin.py`

## 相关页面

- [[benchmark-onboarding-checklist]]
- [[vohu-evals-architecture]]
- [[scoring-evidence]]
- [[vohu-evals-test-strategy]]

## 交互赛道运行边界

τ² 的工具传输与模拟器/Judge 分离；参考模型、预算、运行镜像和工具桥进入冻结证据。SWE 的题库字段必须匹配固定 evaluator，实例环境在模型调用前准备。工作区保留官方编译产物，Agent 工具环境与隐藏评分容器分离，评分容器禁止联网。基础设施错误标记为 system_failed，不产生正式准确率。实际账单只接受匹配请求 ID、模型和已结算标志的精确金额；估算与预留不能替代实际账单。
