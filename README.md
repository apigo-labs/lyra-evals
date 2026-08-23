# VOHU Evals

VOHU 专用、可复现的评测与对外报告流水线。正式被测请求只通过 APIGO Gateway 的 OpenAI Chat Completions
协议调用 VOHU 模型；内部 composition 仅允许 DeepSeek、GLM、Kimi、Qwen 等中国模型。Claude、GPT、Gemini
等成绩只作为人工核验的公开参考，不由本项目调用。

## 首版赛道

- GPQA Diamond
- HLE 纯文本子集
- IFEval
- DRACO Web Research

这些赛道分别报告，不合成总分。IFEval 已冻结 541 条官方数据和 Google evaluator，可进入正式运行；GPQA/HLE
等待合法的数据授权，DRACO 等待自有 judge 协议，因此三者仍 fail closed。

## 快速开始

```bash
uv sync --all-groups
uv run vohu-eval validate
uv run vohu-eval fixture-run gpqa
uv run vohu-eval campaign-plan configs/campaigns/text-v1.yaml
uv run vohu-eval preflight configs/campaigns/browsecomp-v2.yaml \
  --profile vohu-research-v2
uv run vohu-eval dataset ifeval --execute
uv run vohu-eval evaluator ifeval --execute
uv run vohu-eval readiness ifeval
uv run vohu-eval run ifeval --profile vohu-quality-v1 --stage smoke \
  --max-usd 10 --max-requests 60 --max-wall-time-seconds 3600
make verify
```

不带 `--execute` 的 preflight/run 只输出计划。真实运行需专用 Benchmark Workspace/API Key，以及 Platform
JWT 审计凭据；必须额外传入 `--execute --confirm-budget-usd <与 max-usd 完全相同>`。Gateway body 始终保持
官方 OpenAI Chat Completions 格式；Web Research 只增加标准 `web_search_options: {}`。attempt、provider-native
usage 与最终费用通过 execution id 在 Platform 日志接口带外审计，不向标准响应注入私有字段。默认命令不会
发起付费请求。

发布级运行默认要求 Platform 已完成费用结算。本地基础设施缺少结算事件时，可仅为内部 smoke 显式设置
`VOHU_EVALS_ALLOW_UNSETTLED_COST=1`；此时 usage 与 attempt 仍须完整，费用固定记录为不可用的 `0`，不得
将该值解释或发布为零成本。

`targets/` 保存本地 allowlist 与 composition 快照，整个目录被 Git 忽略。基础离线验证不要求该目录存在；
`fixture-run`、`preflight`、`run` 和依赖 profile 的诊断命令需要在执行前由受控来源将 target 文件物化到本地。
仓库不提供真实 target 模板或快照。需要使用其他受控目录时，通过 `VOHU_EVALS_TARGETS_DIR` 指定。

BrowseComp 当前冻结 `vohu-research-v2`：`glm-5.2` 搜索候选与 `kimi-k3` 非搜索候选交给
`minimax-m3` 综合。v1 配置与历史 run 保留不改，不与 v2 aggregate 混合。

运行凭据使用以下环境变量，禁止写入仓库：

- `VOHU_EVALS_GATEWAY_BASE_URL`、`VOHU_EVALS_API_KEY`
- `VOHU_EVALS_PLATFORM_BASE_URL`、`VOHU_EVALS_PLATFORM_TOKEN`
- `VOHU_EVALS_WORKSPACE_ID`
- `VOHU_USER_EMAIL`、`VOHU_USER_PASSWORD`（JWT 自动刷新备用凭据）

`VOHU_EVALS_PLATFORM_BASE_URL` 填 Website 对外 origin 加 `/platform`，例如
`https://website.example.com/platform`。评测器会请求
`/platform/api/v1/logs/detail`，由 Website Nginx 代理到 Platform；不要求 Platform 单独暴露公网地址。
本地可复制 `.env.example` 为已被 Git 忽略的 `.env` 后填写实际值。CLI 自动加载该文件，但不会覆盖 shell
显式注入的同名环境变量。评测器在付费请求前检查 JWT 的 `exp`；缺失、无效或 60 秒内到期时，通过
`/platform/api/v1/auth/login` 登录并原子更新本地 token。审计接口若返回 401，只再刷新重试一次；403、MFA
或重复 401 均停止执行。账号、密码和 JWT 不进入日志、ledger、报告或 Git。

## 目录

- `src/vohu_evals/`：共享运行内核、Gateway、SQLite ledger、报告生成。
- `benchmarks/`：每个 benchmark 的 manifest、插件、fixtures 与官方参考快照。
- `targets/`：本地 allowlist 与 composition 运行输入，不进 Git。
- `configs/`：campaign、profile 与预算配置。
- `runs/`：本地原始工件，不进 Git。
- `reports/`：人工批准后可提交的报告。
- `publications/`：显式 Website 发布包。
- `context-kg/`：项目知识库。
