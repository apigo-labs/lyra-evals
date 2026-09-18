# 项目组织层次

本文档面向新接手的人，目标是 10 分钟内搞清楚：每个目录是干什么的、哪条路径是现在真正在跑的、哪些是历史遗留、数据从哪来到哪去。写作时只读代码和文档头部说明，不猜目录名含义。

## 总览

`lyra-evals` 通过 APIGO Gateway 比较 Fusion 路由（`apigo/lyra-*`）和单模型在 IFEval、GPQA、LiveCodeBench 等赛道上的表现。项目里实际存在**两条执行路径**：

1. **Inspect AI 直连路径（当前主用）**：`scripts/inspect_eval.sh` 在独立 `uvx` 环境里跑 Inspect AI 官方评测器，直连 APIGO Gateway 的 OpenAI Chat Completions 协议，单请求、无 Agent、无工具。`scripts/run_calibration.sh` 是这条路径上跑一整套矩阵（7 变体 × 3 赛道）的封装，`scripts/inspect_report.py` 把日志和账单拼成对比报告。README 中「直连 API 剖面（Inspect AI）」一节明确说这是路演设计复用 Inspect、不在 Console 内另写 harness。
2. **`src/vohu_evals/` 自有 runner / Console / ACP 路径（早期，现状分层）**：CLI（`vohu-eval`）驱动的 campaign/profile/composition 评测体系，以及 `console/` 前端 + `src/vohu_evals/console/` 后端组成的本地 Console，走 Docker/ACP（Claude Agent ACP、Codex ACP）调度。README 原文写「当前可运行真实 Docker 中的合成 ACP 自检……不是五个 benchmark 的正式成绩。正式运行在官方任务适配、Gateway 唯一出口与预算对账完成前会明确拒绝」——即这条路径的基础设施在跑，但没有被判定为可用于正式出成绩，τ²-bench 和 SWE-bench 的完整 Agent 交互「仍未开放」。

两条路径共享同一个 Gateway 出口和同一份 `.env` 凭据，但评分口径、数据来源、报告产物是分开的，不要混用。

## 数据流（文字版）

**Inspect 直连路径（主用）：**

```
题集冻结
  scripts/freeze_inspect_samples.py（seed 固定，按 .local/designs/fusion-roadshow-evaluation.md 抽样）
  → .local/inspect-samples/（校准集 + 主测试集，互斥，官方 id 对齐）
        ↓
跑批
  scripts/inspect_eval.sh（单个任务/模型）
  scripts/run_calibration.sh（TASKS 矩阵封装，SET=calibration|main）
  → .local/inspect-logs/{calibration,main,...}/*.eval
        ↓
平台账单导出（只读）
  scripts/platform_logs_export.py → .local/platform-logs*.json
        ↓
报告生成
  scripts/inspect_report.py（读 .eval 日志 + 账单，逐题复用 scripts/inspect_summary.py）
  → .local/inspect-report*/：report.json、report.csv、samples.csv、report.html
        ↓
对外材料（人工审核后）
  → reports/（只放通过 verifier + 人工审核并标记 published 的报告）
  → publications/（显式 Website 发布包，由已批准报告生成，不自动部署）
```

**VOHU CLI / Console 路径（并行存在，未产出正式成绩）：**

```
benchmarks/*/plugin.py + benchmarks/*/benchmark.yaml（题目/评测器定义）
        ↓
configs/campaigns、configs/profiles、configs/strategies（跑法配置）
        ↓
vohu-eval dataset / evaluator / campaign-plan / preflight / run（CLI，src/vohu_evals/*）
   或 Console（console/ 前端 + src/vohu_evals/console/ 后端）经 Docker/ACP 调度
        ↓
src/vohu_evals/ledger.py（SQLite 账本）+ .local/console/console.sqlite3
        ↓
src/vohu_evals/reporting.py / scripts/score_benchmark.py → 未走 Inspect 报告管线的产物
```

两条路径都不产生「合成总分」，各赛道分别报告。

## 目录树（一句话，只到二级）

```
lyra-evals/
├── README.md                 项目说明：Console、CLI、赛道现状、Inspect 直连剖面、报告口径
├── AGENTS.md                  项目定位与强制边界（Gateway 唯一出口、composition 白名单、禁止提交题库/凭据等）
├── Makefile                    所有入口命令（sync/lint/test、console-*、benchmark-*、inspect-*、calibration）
├── pyproject.toml              包定义；wheel 同时打包 src/vohu_evals 与 src/instruction_following_eval
├── .env / .env.example         Gateway、Platform 审计、账号密码凭据（.env 被 git 忽略）
├── src/
│   ├── vohu_evals/              自有评测内核：CLI、Gateway/Judge 适配器、ledger、报告、console 子包
│   └── instruction_following_eval/  冻结的 Google 官方 IFEval 评分器原文，仅避免包名冲突
├── inspect_tasks/               仓库本地 Inspect AI 任务（目前只有 LiveCodeBench release_v6 薄包装）
├── scripts/                     所有可执行入口：跑批、冻结样本、出报告、账单导出、基准设施准备/验收
├── benchmarks/                  各赛道的 manifest、plugin、fixtures、官方参考快照（VOHU CLI 路径用）
├── configs/                     campaign / profile / strategy 配置（VOHU CLI 路径用，历史版本多）
├── prompts/                     报告/诊断用的 Markdown 提示词模板
├── schemas/                     evidence / official-reference 的 JSON Schema
├── context-kg/                  项目知识库：架构、质量规范、任务记录，长期归档
├── deploy/                      Console 的 Docker 镜像定义（Gateway、ACP、smoke）与各赛道离线 grader 镜像
├── console/                     本地 Console 前端（React + Vite）源码与构建产物
├── publications/                对外 Website 发布包（空壳 + README，由已批准报告生成）
├── reports/                     人工审核通过、标记 published 的报告（空壳 + README）
├── tests/                       pytest 用例，覆盖 src/vohu_evals 与 scripts 的行为
├── .github/workflows/ci.yml     CI：uv sync + make verify
└── .local/                      git 忽略的运行数据目录（见下）
```

## 各顶层目录详解

### `src/vohu_evals/`
自有评测内核，VOHU CLI 和 Console 共用。核心模块：`cli.py`（`vohu-eval` 入口）、`gateway.py`/`judge.py`（APIGO Gateway 与 judge 适配器）、`ledger.py`（SQLite 账本，预算预留/结算）、`config.py`（campaign 校验与 canonical hash）、`benchmark.py`/`dataset.py`/`evaluator.py`（题目加载与评分资源缓存）、`platform_auth.py`（Platform JWT 自动刷新）、`policy.py`（中国模型 composition 白名单校验）、`readiness.py`/`recovery.py`/`rescoring.py`/`reporting.py`（就绪门禁、故障恢复、重评分、报告生成）。`suites/` 子目录是各赛道对 runner 的适配层（`adapters.py`、`packs.py`、`registry.py`、`swe_live.py`）。`console/` 子目录是 Console 后端（FastAPI）：`server.py`、`acp.py`（ACP 调度）、`billing.py`/`budget.py`（预算账本，README 提到「独立预算账本已具备原子预留与幂等结算，但尚未连接真实 Gateway 收费」）、`planner.py`/`preparation.py`/`results.py`/`store.py`/`tau_runtime.py`。
**状态**：活跃维护（有测试、CI 覆盖），但正式出成绩的门禁（官方任务适配、Gateway 唯一出口、预算对账）尚未打通，README 原文明确「正式运行……会明确拒绝」。

### `src/instruction_following_eval/`
冻结的 Google Research IFEval 评分器原文（见 `UPSTREAM.md`：commit `015539128d9a7dbe14b5f5308a198a15da808949`，Apache 2.0），项目只在 `benchmarks/ifeval/plugin.py` 做适配，不改评分语义。
**存在原因**：这个目录本身不参与 Inspect 直连路径的评分——`inspect_evals` 的 IFEval scorer 需要 josejg fork（包名同样是 `instruction_following_eval`），如果把本项目整体装进 Inspect 的隔离 `uvx` 环境，会把这个冻结的 Google 原版一起装进去，遮蔽 fork 里 IFEval 评分器所需的分支。所以 `scripts/inspect_eval.sh` 只对 `inspect_tasks/` 任务设置 `PYTHONPATH=src:.`，不安装本项目到隔离环境；`pyproject.toml` 里 ruff 也把这个目录排除在 lint 之外。它只服务 VOHU CLI 路径（`benchmarks/ifeval/plugin.py`）。

### `inspect_tasks/`
Inspect 直连路径的仓库本地任务，目前只有 `livecodebench_v6.py`：因为 `inspect_evals` 不自带 LiveCodeBench release_v6，这是一个薄包装——题目取自 `.local/suites/livecodebench` 冻结包，提示词复用 `vohu_evals.suites.packs.livecodebench_prompt`（与 ACP 路径同源），评分复用既有断网 Docker grader。**活跃**，是 Inspect 主路径三个赛道之一。

### `scripts/`
所有可执行入口，按用途分组（每个脚本一句话，取自文件头部）：
- Inspect 直连路径：`inspect_eval.sh`（单任务/模型跑批，独立 uvx 环境）、`run_calibration.sh`（7 变体×3 赛道矩阵封装）、`freeze_inspect_samples.py`（固定 seed 分层抽样，产出校准集/主测试集）、`inspect_summary.py`（.eval 日志拉平成逐题行）、`inspect_report.py`（成本/耗时/准确率对比报告）、`platform_logs_export.py`（导出 Platform 账单，只读）、`probe_lyra_protocols.py`（跑前探测模型协议，会产生最小付费请求）。
- 基准设施准备/验收（无模型调用，或显式 opt-in 的零推理验收）：`prepare_benchmarks.py`（准备五项官方数据，无模型调用）、`prepare_benchmark_runtimes.py`（装 τ²/SWE 官方依赖到独立环境）、`build_tau_runtime.py`/`verify_tau_runtime.py`（构建/验收 τ² 运行时）、`swe_runtime.py`（冻结 SWE v4.1 规范 + 离线 grader）、`verify_benchmark_runtime.py`（opt-in 官方基准验收）、`verify_console_runtime.py`（Docker/ACP 基础设施验收）、`score_benchmark.py`（保存答案离线评分，无模型调用）。
- 通用/CI：`secret_scan.py`（密钥扫描）、`validate_context_kg.py`（context-kg frontmatter 校验）。
**状态**：全部活跃，是 Makefile 各目标的实际落点。

### `benchmarks/`
VOHU CLI 路径下每个赛道的定义：`ifeval/`、`gpqa/`、`hle/`、`browsecomp/`、`draco/`、`frames/`，每个子目录有 `benchmark.yaml`（manifest）、`plugin.py`（适配逻辑）、`fixtures/`（离线测试用小样本）、`references/`（官方参考快照）、`README.md`。README 中「首版赛道」只提 GPQA/HLE/IFEval/DRACO 四个，`browsecomp`/`frames` 是曾经的候选或历史配置（`configs/campaigns/` 下有 `browsecomp-v1..v7`、`frames-development-v1..v13`，版本号密集但正式赛道列表里已不再出现，判断为迭代试验遗留，建议清理时确认）。
**状态**：ifeval/gpqa/hle/draco 与当前赛道定位对应，视为活跃；browsecomp/frames 判断为历史遗留（见下方分类表的依据）。

### `configs/`
`campaigns/`（跑法参数，含 `text-v1.yaml`、`web-research-v1.yaml`、`ifeval-auto-v1.yaml`，以及上面提到的 browsecomp/frames 系列版本）、`profiles/`（`vohu-quality-v1` 等预算/效果策略，README 快速开始示例直接引用 `vohu-quality-v1`、`vohu-research-v2`）、`strategies/`（5 个 accuracy-* 策略文件）。版本号从 v1 一路到 v21（profiles）、v13（frames campaigns）说明这是长期迭代产生的配置堆积，多数旧版本大概率不再被引用，但没有逐一核实调用方，未在文档中判定删除。

### `prompts/`
四个 Markdown 提示词模板，供报告/诊断脚本引用（`report.md`、`report-repair.md`、`browsecomp-smoke-report.md`、`smoke-diagnostic-report.md`）。与 `src/vohu_evals/reporting.py` 及 Console 侧生成流程配合。

### `schemas/`
`evidence.schema.json`（跑批证据的 JSON Schema，含 `score_percent`/`total_cases`/`total_cost_usd` 等必填字段）、`official-reference.schema.json`。供 VOHU CLI 路径的报告/校验环节引用。

### `context-kg/`
项目知识库，AGENTS.md 明确要求「所有长期设计、质量规范和任务记录归档到 context-kg/，并维护索引与变更日志」。结构：`_meta/`（`index.md` 总索引、`log.md` 变更日志、`schema.md` 文档规范）、`technical/adr/`（架构决策记录，如 `vohu-evals-architecture.md`、`fusion-router-evaluation-design.md`、`local-console-execution-design.md`，部分标注「未实现」「待实现」）、`quality/`（`benchmark-integration.md`、`ifeval-statistical-reporting.md`、`vohu-evals-test-strategy.md` 等评分与测试规范）、`tasks/`（`todo.md`、`lessons.md`）。
**状态**：活跃，是理解设计意图的第一手资料，比代码注释更完整；但部分 ADR 本身记录的是「提案」而非已落地状态，读的时候要对照 README 现状。

### `deploy/`
容器化定义，非 Python 包代码。`console/`：`gateway/`（Node 出口策略代理，`policy.mjs`+`policy.test.mjs` 有独立测试，README 提到 `node --test deploy/console/gateway/policy.test.mjs`）、`claude-acp/`、`codex-acp/`（冻结版 ACP Agent 镜像）、`smoke/`（合成自检适配器，不调用模型）。`suites/`：`livecodebench/`（断网官方评分镜像）、`tau-runtime/`、`swe-runtime/`（各自的 Docker + MCP bridge worker）。
**状态**：活跃，是 Console/ACP 路径和三个基准官方评分器的运行时依赖，通过 Makefile 的 `*-image` 目标构建。

### `console/`
本地 Console 前端源码（React + Vite + Tailwind + shadcn 风格组件，`components.json`/`src/components/ui`），`src/` 下 `App.tsx`、`Billing.tsx`、`PrepareEnvironment.tsx`、`PriceCap.tsx`、`Results.tsx` 对应 README 描述的「新建评测」「结果与导出」等页面。`dist/`、`node_modules/` 是构建产物/依赖，不必看。
**状态**：活跃开发中，但功能边界受限——README 原文：「τ² 和 SWE 的完整 Agent 交互仍未开放；最终账单自动对账尚未接通」。

### `publications/`、`reports/`
两者都只有一个 README 占位说明，目录本身是空壳（内容被 `.gitignore` 排除或从未生成）：`reports/` 只放通过 verifier + 人工审核并标记 `published` 的报告，草稿被 `.gitignore` 排除；`publications/` 是显式生成的 Website 发布包，不自动部署。
**状态**：机制活跃（Inspect 报告管线的终点），但仓库里当前没有已发布内容，正常。

### `tests/`
pytest 用例，按模块/功能一一对应，不是按赛道分：`test_cli.py`、`test_config.py`、`test_gateway.py`、`test_judge.py`、`test_ledger`（经 `test_recovery.py`/`test_rescoring.py`/`test_readiness.py`/`test_audit.py`/`test_policy.py` 覆盖相关逻辑）、`test_dataset.py`/`test_benchmarks.py`/`test_suite_integrations.py`/`test_browsecomp.py`/`test_draco.py`/`test_frames.py`/`test_gpqa_offline_scoring.py`（各赛道适配）、`test_console*.py`（Console 后端：billing/live/planner/preparation/results）、`test_inspect_report.py`/`test_inspect_lcb_task.py`/`test_freeze_inspect_samples.py`（Inspect 直连路径）、`test_secret_scan.py`/`test_strategy_candidates.py`/`test_reporting.py`/`test_runner.py`。`pyproject.toml` 里 `pythonpath = ["."]` 让 `inspect_tasks/` 在测试里可导入。
**状态**：活跃，`make verify` 的 `test` 目标跑全部。

### `.github/workflows/ci.yml`
唯一的 CI 工作流：`uv sync --frozen --all-groups` 后跑 `make verify`（lint + test + context-kg 校验 + secret scan + `git diff --check`）。不跑基准准备、不跑 Inspect、不碰 Docker，符合 AGENTS.md「CI 和默认命令不得产生付费模型调用」的约束。

### `.local/`（git 忽略，运行数据，不列具体文件）
按子目录角色：
- `inspect-samples/`：冻结的校准/主测试题集（Inspect 路径起点）。
- `inspect-logs/{calibration,main,main-partial,archive-terra,stream-verify}/`：Inspect 跑批原始日志，按 SET 分子目录，`archive-terra` 看名字像是某次跑批的归档保留。
- `inspect-report/`、`inspect-report-main/`、`inspect-report-scoring-test/`：`inspect_report.py` 的输出，对应不同 LOGS/OUT 组合，其中 `-scoring-test` 像是调试产物。
- `platform-logs*.json`（未列出但由 Makefile 变量 PLATFORM 指向）：账单导出缓存。
- `suites/`：官方题库冻结数据（`ifeval`、`gpqa`、`livecodebench`、`swebench`、`tau2`，及 `downloads/`、`variants/`）。
- `upstream/`：外部依赖的本地克隆（`instruction_following_eval` fork、`livecodebench`、`swebench`、`tau2`），供 `inspect_eval.sh` 和 `prepare_benchmark_runtimes.py` 优先复用，避免跑批时依赖 GitHub 可达性。
- `console/`：Console 本地状态，含 `secrets/` 和 `console.sqlite3`（VOHU CLI/Console 路径的账本与计划存储）。
- `designs/`：跑批设计文档（`fusion-roadshow-evaluation.md` 是抽样与矩阵设计的权威来源，`freeze_inspect_samples.py`/`run_calibration.sh` 均引用它）、`swe-bench-verified-research.md`。
- `acceptance/`：`verify_benchmark_runtime.py` 等验收脚本的产物（SWE check、negative case 等）。
- `benchmark-runtime/`、`build/`、`hf-cache/`、`swe-specs/`、`rerun-logs/`、`execution/`、`research/`：基准运行时依赖安装、HuggingFace 数据集缓存、SWE 规格、重跑日志、执行中间态、外部基线研究笔记。
本文档不做逐文件说明；接手者需要具体内容时直接 `find`/`cat`，不要提交任何 `.local/` 下的内容到 Git（AGENTS.md 强制约束）。

## 活跃 / 保留 / 计划移除 分类

| 分类 | 内容 | 依据 |
|---|---|---|
| 活跃 | `src/vohu_evals/`（含 `console/`、`suites/`）、`scripts/`、`inspect_tasks/`、`tests/`、`.github/workflows/ci.yml`、`deploy/`、`console/`、`context-kg/`、`benchmarks/ifeval`、`benchmarks/gpqa`、`benchmarks/hle`、`benchmarks/draco` | README/AGENTS.md 直接引用，Makefile 有对应目标，测试覆盖，或是 Inspect 主路径三赛道之一 |
| 保留但边界受限 | Console 的 ACP/Docker 执行链路本身（`src/vohu_evals/console/*`、`deploy/console/*`） | README 明确写「不是五个 benchmark 的正式成绩」「τ²/SWE 完整 Agent 交互仍未开放」；代码在维护，但产出不能当正式结果用 |
| 历史遗留（判断依据见下） | `benchmarks/browsecomp/`、`benchmarks/frames/`、`configs/campaigns/browsecomp-v*.yaml`（7 个版本）、`configs/campaigns/frames-development-v*.yaml`（13 个版本）、`configs/profiles/vohu-research-v3..v21` 中大部分未被脚本/文档引用的版本 | README「首版赛道」清单只列 GPQA/HLE/IFEval/DRACO，不含 BrowseComp/Frames；这两个 plugin 目录和对应大量版本化 campaign 配置在当前赛道范围之外，属于早期探索留下的迭代产物。**未逐一核实是否被测试或脚本间接引用**（`tests/test_browsecomp.py`、`tests/test_frames.py` 仍存在，说明代码路径还在跑测试，不建议直接删除，只建议决策者复核） |
| 需人工复核（可能是调试产物） | `.local/inspect-report-scoring-test/`、`.local/inspect-logs/archive-terra/`、`.local/inspect-logs/main-partial/` | 命名暗示是调试/中断/归档产物，但 `.local/` 不提交 Git，不影响仓库整洁度，只在磁盘清理时需要人工判断是否还需要 |

## 常见任务对应入口

- **跑一组评测（Inspect 直连，日常主用）**：单任务用 `make inspect-eval TASK=... MODEL=... ARGS=...`（落地在 `scripts/inspect_eval.sh`）；整套矩阵用 `make calibration` 或 `SET=main scripts/run_calibration.sh`（落地在 `scripts/run_calibration.sh`，变体/模型列表在该脚本的 `DEFAULT_VARIANTS`）；跑前如需先固定题集，用 `scripts/freeze_inspect_samples.py`。
- **出报告**：先 `make platform-logs-export` 刷新账单，再 `make inspect-report LOGS=... PLATFORM=... OUT=...`，落地在 `scripts/inspect_report.py`（逐题拉平逻辑在 `scripts/inspect_summary.py`）。
- **加新赛道**：
  - 走 Inspect 路径且 `inspect_evals` 已支持：直接给 `make inspect-eval` 传对应 `TASK`，不需要改代码。
  - 走 Inspect 路径但官方库不支持（如 LiveCodeBench 那种情况）：参照 `inspect_tasks/livecodebench_v6.py` 写一个薄包装任务，数据/评分尽量复用 `src/vohu_evals/suites/` 现成逻辑。
  - 走 VOHU CLI/Console 路径：在 `benchmarks/<name>/` 下新增 `benchmark.yaml` + `plugin.py` + `fixtures/`，参照 `context-kg/quality/benchmark-onboarding-checklist.md` 与 `context-kg/quality/benchmark-integration.md` 的接入规范。
- **改计分口径**：Inspect 路径的准确率/损耗/基线定义在 `scripts/inspect_report.py`（README「计分口径」「成本口径」两节是权威文字说明，改代码前先读这两节）；VOHU CLI 路径的评分逻辑在各 `benchmarks/<name>/plugin.py` 和 `src/vohu_evals/evaluator.py`/`rescoring.py`。

## 凭据与配置

- `.env`（git 忽略）/ `.env.example`：`VOHU_EVALS_GATEWAY_BASE_URL`、`VOHU_EVALS_API_KEY`（Gateway Key，两条路径共用）、`VOHU_EVALS_PLATFORM_BASE_URL`/`VOHU_EVALS_PLATFORM_TOKEN`/`VOHU_EVALS_WORKSPACE_ID`（Platform 账单审计）、`VOHU_USER_EMAIL`/`VOHU_USER_PASSWORD`（JWT 过期时走 Platform 登录接口自动刷新的备用凭据）。CLI 和脚本自动加载 `.env`，但不覆盖 shell 显式注入的同名变量。
- `configs/`：campaign（跑法）、profile（预算/效果策略）、strategy（候选策略）三类 YAML，只服务 VOHU CLI/Console 路径；Inspect 路径的参数直接写在 `scripts/run_calibration.sh` 的 `TASKS`/`VARIANTS` 里，不走这套配置。
- AGENTS.md 强制边界（写文档/改代码前必读）：正式 composition 的 panel/analyst/synthesizer 只能用本地 `targets/china-models-*.yaml`（git 忽略，不得提交真实 allowlist）；普通测试/CI/默认命令不得产生付费调用；题库、凭据、SQLite、原始回答、未批准报告不得提交 Git；对外比较要遵守 `comparable`/`reference_only`/`not_comparable` 三级口径；长期设计归档到 `context-kg/` 并维护索引和变更日志。

## 建议清理（供决策，未执行任何操作）

- `benchmarks/browsecomp/`、`benchmarks/frames/` 及对应的 `configs/campaigns/browsecomp-v1..v7.yaml`、`configs/campaigns/frames-development-v1..v13.yaml`：不在当前「首版赛道」清单内，版本号密集，疑似早期迭代遗留。建议确认是否还有计划复用，否则可以考虑归档说明或标注废弃状态（不建议直接删，`tests/test_browsecomp.py`/`tests/test_frames.py` 还在跑）。
- `configs/profiles/vohu-research-v3.yaml` 到 `v21.yaml`：版本号跨度大，建议核实哪些版本仍被引用（README 快速开始只举例 `vohu-research-v2`），无人引用的旧版本可以归并或加说明注释。
- `.local/inspect-report-scoring-test/`、`.local/inspect-logs/main-partial/`：命名像调试/中断产物，建议确认是否还需要保留，属磁盘清理范畴，不影响仓库。
