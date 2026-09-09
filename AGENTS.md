# AGENTS.md

## 项目定位

`lyra-evals` 经 APIGO Gateway 比较 Fusion 与单模型，GPT 系列执行 Agent 采用冻结版本的 Codex ACP，Claude 系列采用冻结版本的 Claude Agent ACP；其他模型/Fusion 按已验证能力选择，不静默回退。
模型、Harness、effort、工具 profile 与预算共同定义不可变实验变体；跨 Harness 结果不得归因为纯模型差异。
不得绕过 Gateway 直连模型厂商。当前目标为 IFEval、GPQA、LiveCodeBench、τ²-bench、SWE-bench。
旧 VOHU composition 的约束只适用于旧 profile，不限制独立单模型目标。

## 强制边界

- 正式 VOHU composition 的 panel、analyst、synthesizer 只允许本地 `targets/china-models-*.yaml` 中的中国模型；
  `targets/` 是被 Git 忽略的运行输入，不得提交真实 allowlist 或 composition。
- 普通测试、CI 和默认命令不得产生付费模型调用；真实调用必须显式确认预算。
- 题库、凭据、SQLite、原始回答和未批准报告不得提交 Git。
- 对外比较必须遵守 `comparable`、`reference_only`、`not_comparable` 三级口径。
- 所有长期设计、质量规范和任务记录归档到 `context-kg/`，并维护索引与变更日志。
- 文档与协作输出使用中文；对外 News `report.md` 可以使用英文。

## 开发与验证

- Python 3.12，依赖由 `uv.lock` 冻结。
- 优先从深模块公开接口测试行为，不跨过 seam 断言内部状态。
- 完成前运行 `make verify`。
