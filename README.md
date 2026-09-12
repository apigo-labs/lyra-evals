# Lyra Evals

## 本地 Console

新工作台用于配置 APIGO 模型、管理评测计划、观察 Docker/ACP 并行执行。完整设计见
[本地 Console 与执行设计](context-kg/technical/adr/local-console-execution-design.md)。

```bash
make console-install
make console-smoke-image
make console
```

打开 `http://127.0.0.1:8768`。开发前端可另运行 `make console-dev`，使用 `127.0.0.1:5178`。
`make console-acp-image` 构建冻结的 Claude Agent ACP 镜像。

当前可运行真实 Docker 中的合成 ACP 自检，支持评测集内并发、跨评测集并发和取消；自检不调用模型，
不是五个 benchmark 的正式成绩。正式运行在官方任务适配、Gateway 唯一出口与预算对账完成前会明确拒绝。
模型配置（名称/协议/Endpoint/模型 ID）保存于 `.local/console/`；凭据不落在这些文件里，而是复用
CLI 的 `.env`（`VOHU_EVALS_API_KEY` 作为所有连接共用的 Gateway Key，`VOHU_EVALS_PLATFORM_BASE_URL`
/`VOHU_EVALS_WORKSPACE_ID`/`VOHU_EVALS_PLATFORM_TOKEN`/`VOHU_USER_EMAIL`/`VOHU_USER_PASSWORD` 用于平台
账单对账，令牌过期时自动登录刷新），变量名与 `.env.example` 一致。凭据不回显、不写入浏览器持久化存储。
不要将本地控制器暴露到公网。

## 既有 VOHU CLI

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

### 模型与 effort 计划

Console 的「新建评测 → 真实模型评测」可按连接选择 Harness 与多个 effort，配置预算策略、输出上限、时限和 seed；「生成并保存计划」只执行离线校验。
`GET /api/capabilities` 返回未验证候选；`POST /api/plans` 展开去重矩阵；`GET /api/plans` 和 `GET /api/plans/{id}` 读取冻结计划。
计划支持 JSON 导出。API 不回传凭据，修改配置产生新 hash。未知价格不填零，未通过数据、网络与计费门禁的计划不能执行。
GPT 配置使用 Codex/Responses；Claude 使用 Claude Agent/Messages。候选 effort 不等于模型已支持。
独立预算账本已具备原子预留与幂等结算，但尚未连接真实 Gateway 收费；Codex 的正式任务执行和结果曲线仍待接入。

### 五个评测集的数据与评分入口

`make benchmark-runtimes` 安装固定源码版本的 τ²/SWE 官方依赖到独立环境；`make benchmark-prepare` 准备五项数据；`make benchmark-lcb-image` 构建断网 LCB 官方评分镜像。
数据保存在 `.local/suites`，包含不可变 SHA-256、来源版本和去重 case IDs；新计划冻结具体抽样 IDs。GPQA 默认使用作者公开发布包，也支持 `--gpqa-csv` 导入授权文件。LCB 原始文件逐行处理，隐藏测试独立保存，并核对官方文件 SHA-256。

| 评测集 | 当前数据范围 | 代码入口 | 正式执行剩余项 |
|---|---|---|---|
| IFEval | 官方 541 题 | 原官方 strict/loose scorer，保存答案评分 CLI | ACP 调度与计费 |
| GPQA | Diamond 198 题 | CSV 导入、固定 shuffle、严格最终选项解析 | ACP 调度与计费；本地提示为派生闭卷协议 |
| LiveCodeBench | release_v6 lite，1055 题 | 官方测试执行器、独立断网 Docker grader | ACP 调度与计费 |
| τ²-bench | retail/airline/telecom base，278 题 | 官方 Gym 会话适配，隐藏状态隔离 | ACP 工具传输、用户模拟器/Judge Gateway 计费 |
| SWE-bench | Verified 500 题 | patch predictions、官方 Docker evaluator、结果解析 | 实例镜像验收、编码 Agent 调度与计费 |

Console 读取实际本地数据状态；上述接口接入不意味着已完成五项端到端付费测评。`scripts/score_benchmark.py` 支持保存答案的离线评分或 SWE 官方执行入口；`scripts/verify_benchmark_runtime.py` 是显式运行的无模型验收。默认 `make verify` 不读取真实题库、不启动 Docker、不调用模型。


### 结果总览与真实小样本验收

Console「结果与导出」集中查看成本、单题耗时、准确率、Harness 与 effort；支持作业 CSV/JSON 和逐题 CSV。未知费用为空，公开价格上界估算与实扣账单分列；自检不作为模型成绩。点击作业进度查看逐题证据。

构建 `make console-gateway-image` 后，可在冻结计划中授权预算并运行 IFEval、GPQA、LiveCodeBench。控制器要求所有单题预算之和不超过实验授权，冻结官方数据/镜像/价格上界，并通过每题专属转发器只访问 APIGO。τ² 和 SWE 的完整 Agent 交互仍未开放；最终账单自动对账尚未接通。

离线出口策略测试：`node --test deploy/console/gateway/policy.test.mjs`。普通测试不产生模型调用。

### 直连 API 剖面（Inspect AI）

Fusion 路由 `apigo/lyra-*` 只接受 OpenAI Chat Completions，无法由 Claude Agent / Codex ACP 驱动。路演设计中的直连剖面复用 Inspect AI，
不在 Console 内另写 harness：

```bash
make inspect-eval TASK=inspect_evals/ifeval MODEL=apigo/lyra-auto ARGS="--limit 10 --max-tokens 8192"
make inspect-eval TASK=inspect_evals/gpqa_diamond MODEL=gpt-5.6-luna ARGS="--reasoning-effort high --limit 10 --max-tokens 16384"
make inspect-eval TASK=inspect_tasks/livecodebench_v6.py MODEL=apigo/lyra-auto ARGS="--limit 10 --max-tokens 16384"
make inspect-summary ARGS="--csv .local/inspect-summary.csv"
```

包装脚本在 `uvx` 独立环境中固定 inspect-ai 与 inspect-evals 版本，凭据从 `.env` 的 `VOHU_EVALS_API_KEY` 映射，日志写入
`.local/inspect-logs`。汇总脚本导出逐题用量、耗时、响应 id 与 Lyra 路由字段（路由到的模型、难度、任务类型）。Lyra 的
usage 含内部路由开销，输出上限只是预留边界；账单以 execution id 对账为准。`scripts/probe_lyra_protocols.py` 可在冻结计划前用最小请求确认任意模型的协议。

LiveCodeBench release_v6 不在 inspect_evals 中，由仓库内的 `inspect_tasks/livecodebench_v6.py` 薄包装接入：题目来自
`.local/suites/livecodebench` 冻结题库（任务 metadata 记录 pack sha256），提示词与 ACP 路径同源，评分仍是既有断网 Docker
grader，单轮生成、无工具、无重试。运行前需 `make benchmark-lcb-image` 并保证 Docker 可用；grader 本身失败（Docker 缺失、
超时、输出非法）会作为 Inspect 的 sample error 上报，不计为模型答错。包装脚本只在 `inspect_tasks/` 任务上设置
`PYTHONPATH=src:.`，不把本项目装进独立环境，避免 `instruction_following_eval` 同名包遮蔽 IFEval 评分器所需的分支。

- `scripts/freeze_inspect_samples.py` 按 `.local/designs/fusion-roadshow-evaluation.md` 第 3 节固定 seed 20260909，从冻结 pack（IFEval/GPQA Diamond/LiveCodeBench release_v6）分层抽取互斥的校准集与主测试集，官方 id 对齐 `--sample-id`（IFEval 用 `key`，GPQA 用 CSV `Record ID`，LCB 用 pack case id），排除 `.local/console/console.sqlite3` 与 `.local/inspect-logs/*.eval` 中已用过的题目，输出到 `.local/inspect-samples/`（默认拒绝覆盖已冻结结果，需 `--force`）。
