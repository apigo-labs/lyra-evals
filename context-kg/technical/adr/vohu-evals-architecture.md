---
title: VOHU Evals 运行架构
tags: [architecture, evaluation, vohu]
links: [benchmark-integration, scoring-evidence, vohu-evals-test-strategy]
updated: 2026-08-24
sources: 7
---

# VOHU Evals 运行架构

## 决策

`vohu-evals` 是 VOHU 专用评测器，不是通用模型 runner。正式被测请求只通过 APIGO Gateway 调用规范化的
VOHU 模型标识；外部模型不进入目标调用链。

## 模块链路

1. CLI 加载 Benchmark、profile、campaign 和运行阶段，先生成确定性计划。
2. Benchmark plugin 枚举 case、构造请求、解析响应并实现赛道评分。
3. Gateway adapter 只发送标准 Chat Completions 请求；联网赛道通过标准搜索选项声明能力。
4. Audit adapter 在响应协议之外解析 VOHU execution 的 attempts、usage 和结算证据。
5. Policy 校验实际 attempt 模型属于本地 target allowlist，并与冻结 composition 一致。
6. Runner 统一执行预算、动态超时、瞬时失败重试、固定分母、评分和断点续跑。
7. SQLite ledger 保存不可变 run manifest 和每个 case 的终态，再由 reporting 生成 evidence。

题目级并发由 Runner 的冻结参数 `max_concurrency` 控制，默认值为 1，上限为 32。Runner 只并发独立 case，
不介入单题内部的 VOHU 编排。每次目标调用前，ledger 在事务中原子预留 attempt 和全局请求额度；因此并发
worker 不会突破请求上限，进程崩溃后也不会把已发出但尚未落终态的请求当作未消耗额度。Benchmark 的响应
解析和评分保持串行，避免第三方 evaluator 的进程级状态在多线程之间相互污染。

## 核心不变量

- 默认命令和测试不发起真实模型调用；执行与预算必须双重显式确认。
- run manifest 一经写入不可改变；相同 run 标识只能恢复，不能换配置覆盖。
- 并发度进入 run manifest 和 run 标识；改变并发度必须产生新的不可变 run。
- `completed`、`system_failed`、`invalid_output` 都属于固定分母；仍有 pending 时 run 不得关闭。
- composition 审计缺失、出现未允许模型或与冻结集合不符时 fail closed。
- `targets/` 是被 Git 忽略的本地运行输入；默认离线验证不依赖真实 allowlist 或 composition 文件。
- 原始题库、响应、账本和未批准报告只存在于 Git 忽略的本地运行目录。
- Gateway 请求保持公开协议，VOHU 内部审计信息不注入目标请求体。

## 扩展边界

- 新赛道通过 `Benchmark` 子类接入，不向 Runner 添加题库特例。
- 新协议需要新增明确 adapter 和协议版本，不能在现有 adapter 中隐式切换。
- 新评分器必须版本化，并让评分所需的 evaluator 调用进入预算和 evidence。

## 证据

- `src/vohu_evals/cli.py`
- `src/vohu_evals/gateway.py`
- `src/vohu_evals/audit.py`
- `src/vohu_evals/policy.py`
- `src/vohu_evals/runner.py`
- `src/vohu_evals/ledger.py`
- `src/vohu_evals/reporting.py`

## 相关页面

- [[benchmark-integration]]
- [[scoring-evidence]]
- [[vohu-evals-test-strategy]]
