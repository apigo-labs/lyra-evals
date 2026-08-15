# AGENTS.md

## 项目定位

`vohu-evals` 只评测经 APIGO Gateway 调用的 `apigo/vohu`。不得把它扩展为通用模型 runner，也不得绕过
Gateway 直连模型厂商。

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
