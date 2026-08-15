---
title: VOHU Evals 测试与发布门禁
tags: [quality, testing, verification]
links: [vohu-evals-architecture, benchmark-integration, scoring-evidence]
updated: 2026-08-15
sources: 18
---

# VOHU Evals 测试与发布门禁

## 测试分层

1. 单元测试：配置哈希、数据完整性、评分解析、预算、重试、策略和报告声明。
2. Fixture 测试：所有 plugin 使用合成题目和响应验证完整运行链路，不访问外部服务。
3. 契约测试：使用 mock transport 验证 Gateway 请求格式、带外审计、鉴权失败与 ledger 落账。
4. Readiness：检查 plugin 状态、数据/evaluator 冻结、缓存和可比性 blocker。
5. 真实运行：只在显式执行和预算确认后进行，结果仍需通过 evidence 与发布门禁。

## 必测不变量

- 默认命令无网络、无付费调用。
- 运行预算同时约束请求数、费用和墙钟，重试不能绕过预算。
- 相同 run 可以续跑但不能改变 manifest，已完成 case 不得重复调用。
- 系统失败和无效输出保留在固定分母中。
- Gateway body 保持标准协议；attempt 审计、usage 与成本通过独立 seam 注入。
- 实际 composition 必须有审计、属于 allowlist，并匹配冻结 profile。
- 数据下载拒绝摘要不匹配、不安全路径、重复 ID 和错误记录数。
- 派生重评不调用目标、不修改源 run，并保留派生关系。
- 报告中的量化声明必须能由 evidence 路径验证。
- Secret scan 检查所有可提交文件，同时忽略明确排除的本地运行工件。

## 完成条件

任何实现或知识库变更在提交前运行 `make verify`。它必须覆盖格式、静态检查、测试、Context-KG 校验、敏感信息
扫描和 diff whitespace 检查。真实模型运行不属于 `make verify`，不得成为普通 CI 的隐式副作用。

## 证据

- `Makefile`
- `.github/workflows/ci.yml`
- `tests/test_audit.py`
- `tests/test_benchmarks.py`
- `tests/test_cli.py`
- `tests/test_config.py`
- `tests/test_dataset.py`
- `tests/test_gateway.py`
- `tests/test_judge.py`
- `tests/test_platform_auth.py`
- `tests/test_policy.py`
- `tests/test_readiness.py`
- `tests/test_reporting.py`
- `tests/test_rescoring.py`
- `tests/test_runner.py`
- `tests/test_secret_scan.py`
- `tests/test_strategy_candidates.py`
- `tests/test_browsecomp.py`

## 相关页面

- [[vohu-evals-architecture]]
- [[benchmark-integration]]
- [[scoring-evidence]]
