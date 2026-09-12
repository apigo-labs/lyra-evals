---
title: Benchmark 接入清单与 Fusion 能力约束
tags: [quality, benchmark, integration, proposal]
links: [index, benchmark-integration, fusion-router-evaluation-design]
updated: 2026-09-12
sources: 11
---

# Benchmark 接入清单与 Fusion 能力约束

本文区分仓库已实现事实、用户确认约束及拟议接入工作。当前五个评测集保留官方题库、环境和评分；GPT 使用 Codex ACP，Claude 使用 Claude Agent ACP，其他模型/Fusion 依据兼容性验证选用。模型/effort 实验矩阵、预算和跨 Harness 可比性以 [[model-effort-evaluation-design]] 为准，取代此前统一 Claude Code 的决定。历史插件盘点不代表当前接入范围。

## 当前代码增量：五项数据与评分接口

- `scripts/prepare_benchmarks.py` 准备官方数据，记录来源版本、文件哈希与 case IDs；`suites/packs.py` 提供不可变校验和面向 Agent 的字段白名单。
- GPQA 已支持作者公开发布包，不再必须依赖 Hugging Face gated 获取方式；采用独立冻结的闭卷 shuffle/答案协议，原 legacy manifest 不因新适配器存在而自动获得 publication 资格。
- LCB 使用官方测试执行源码与断网 Docker grader；SWE 使用官方 predictions/评估入口与报告解析；τ² 使用官方 Gym 会话桥并隔离隐藏状态。
- Console 读取实际准备状态，计划冻结题目 IDs 和数据哈希。接口接入与完整 ACP 执行分别标记；τ² 模拟器/Judge Gateway 计费、SWE 实例验收及统一正式调度仍未贯通。
- 数据准备和 Docker/官方包验收为显式命令；普通测试仅使用合成输入，不读取真实题库。

## 当前选定范围与按模型选择 Harness 的方案

状态：设计已选定，正式驱动和各模型/effort 预检待实现。按赛道固定工具权限，按 Harness 冻结版本；跨 Harness 结果标为系统参考比较。

| 评测集 | 当前代码基础 | 所选 Harness 执行配置（拟议） | 保留的官方部分 | 主要接入缺口 |
|---|---|---|---|---|
| IFEval | 官方评分已接入 | 无工具、无网络、每题独立上下文；仅提取最终回答，禁止自动修复答案 | 数据、strict/loose evaluator | CLI driver、干净环境、回答提取、Target/账单适配；是 所选 Harness 条件下成绩 |
| GPQA Diamond | 作者公开数据导入、固定 shuffle 与评分 | 闭卷、无工具、无网络；固定提示与选项顺序种子 | 数据及答案协议 | ACP 驱动与费用/隔离；不将 Harness 系统提示影响隐去 |
| LiveCodeBench | 数据准备、官方 Docker 评分接口 | 首版代码生成轨：无工具、无网络，输出代码；公开/隐藏测试由独立 evaluator 执行 | 冻结数据、测试与官方执行评分 | CLI 输出到代码工件、沙箱、版本时间范围；若后续允许自测/修改，另建 agentic 协议 |
| τ²-bench | base 数据、官方 Gym 会话桥 | 所选 Harness 替换被测 agent；仅开放该领域工具与用户交互桥，禁止直接读数据库或任务隐藏状态 | 用户模拟器、领域环境、规则、状态评分 | 双向用户交互桥、工具调用映射、结束/交接语义、环境重置与额外模型成本；不能只包装一次 CLI 问答 |
| SWE-bench | Verified 数据、patch/官方 evaluator 接口 | 所选 Harness 在指定初始仓库运行；允许受控文件编辑、终端和可见测试；禁止访问隐藏验证材料 | 题目、固定环境与 patch evaluator | 独立沙箱、patch 提取、官方验证流程；子集须固定，暂不假定使用全部数据 |

首版不启用 WebSearch/WebFetch，也不依赖 Fusion 内置搜索。IFEval/GPQA/LiveCodeBench 的禁工具规则需要运行时配置和隔离环境共同落实，不能只在提示里要求。模型推理内部的多次调用必须记录，不把“无工具”写成“必然只调用一次模型”。

真实 ACP 小样本验收中 GPQA 曾长期不出分：episode 证据显示模型（尤其 Codex/gpt 系高 effort）在几乎每次响应里都附带一次工具调用尝试（`response.custom_tool_call_input.*` 事件），但该赛道网关按设计拒绝/不提供工具结果，导致 Agent 反复重试、跨 3–4 次请求耗尽 deadline 或 episode 预算，从未产出可解析的最终答案；这是“Agent 循环/多次请求”而非解析器 bug——已发现的唯一评出分的样本文本能被既有严格解析器正确抽取（`Answer: X` 收尾），只是答案本身错。据此在 `suites/packs.py` 的 GPQA prompt 中新增显式闭卷协议句（声明本任务没有工具、禁止调用、要求单轮纯文本作答并给出无 Markdown 修饰的收尾行），解析器同时做了防御性加固（剥离 `*`/`_`/`` ` `` 等修饰符后再按原有“显式最终答案行/独立字母行，取最后一条，含糊即不计分”的严格规则解析，未放宽为“文本任意处出现的字母”）。这只降低了模型主动发起工具调用的概率；网关/沙箱层面是否真的物理隔离工具结果、以及 deadline_seconds/max_output_tokens/episode_budget 默认值是否匹配高 effort 推理耗时，仍需要拥有 planner/live 执行配置的一侧核实，未在此更新。

Claude Code 是执行程序；Claude 模型只是可能的被测目标之一。同 Harness 对照通过 APIGO 将同一程序背后的模型替换为 Fusion/兼容单模型；跨 Harness 对照独立报告，并从审计证据确认实际调用身份。

### Claude Code 接口与实验边界

- [官方 Gateway 文档](https://code.claude.com/docs/en/llm-gateway)明确说明不支持通过 Gateway 将 Claude Code 路由到非 Claude 模型。故非 Claude/Fusion 是待工程验证的兼容适配，不能宣称官方支持；若不兼容，应报告目标不支持并保留失败证据，不静默换 Agent 或回退到 Claude。
- [官方非交互运行文档](https://code.claude.com/docs/en/headless)提供 `claude -p` 和结构化输出入口。实现时固定可用版本及对应参数；清除用户配置、自动记忆、Skills、插件及隐式 MCP，避免机器环境污染。
- 现有 evals adapter 仅发送 Chat Completions；新 driver 需要验证所选 Claude Code Gateway 格式、流式事件、工具消息、错误语义，以及目标需要的协议转换。Gateway 存在 `/v1/messages` 路由不等于 Fusion 已完整兼容。
- 在冻结版本上验证主调用、压缩及辅助请求的模型选择；禁用不需要的 subagent/隐式 fallback，记录全部实际模型和费用，不能只依据 CLI 的主模型参数作比较。
- 协议适配应尽可能保持消息、工具和错误语义；为不同模型添加的提示、修复或功能降级必须进入 manifest。结果解释为“Claude Code + 适配层 + 被测目标”的表现。
- 保留官方 evaluator 不自动保留官方成绩可比性。统一标明 Claude Code 版本、profile、任务预算与差异；外部榜单可比性逐赛道核验。

### Lyra 路由模型只接受 OpenAI Chat Completions

用最小请求（16 token）对三个 Lyra 路由目标逐协议实测（`scripts/probe_lyra_protocols.py`）：

| 协议 | lyra-auto | lyra-budget / lyra-quality |
|---|---|---|
| `/v1/chat/completions` | 200，正常回答，返回 usage | 200，正常回答，返回 usage |
| `/v1/messages` | 502 `Lyra execution failed` | 400 `Lyra protocol unsupported` |
| `/v1/responses` | 502 `Lyra execution failed` | 400 `Lyra protocol unsupported` |

结论：Lyra 目标不能由 Claude Agent ACP（只发 Messages）或 Codex ACP（只发 Responses）驱动；转发器对 Messages 请求体做的参数剥离不解决问题，仅保留为出站形状记录。Lyra 目标在 Console 中的协议应为 `openai_chat_completions`。

两个计费口径差异必须记录：Lyra 返回的 usage 含内部路由开销（极短提示的 prompt_tokens 达数百，回复 "OK" 的 completion_tokens 远超请求的 max_tokens），因此外层输出上限只是预留边界，不是账单边界；Chat Completions 响应附带 `lyra` 字段，含 router/answer 参与模型、难度判定、任务类型、policy 版本与 execution id，是路由证据的权威来源，也是账单对账的请求标识。

### 直连 API 剖面复用 Inspect AI

直连单轮、无工具的剖面不自行实现 harness，复用 Inspect AI 的 `openai-api/<provider>/<model>` 提供者与 inspect_evals 官方任务（`ifeval`、`gpqa_diamond`，均使用官方评分器；`gpqa_diamond` 默认 4 个 epoch，横向比较必须显式单 epoch）。入口为 `scripts/inspect_eval.sh`，在 `uvx` 独立环境中固定 inspect-ai / inspect-evals 版本；独立环境的原因是 inspect_evals 的 IFEval 评分器依赖的 `instruction_following_eval` 分支与本仓库冻结的 Google 原版同名。`scripts/inspect_summary.py` 把 `.eval` 日志展平为逐题用量、耗时、响应 id 与 Lyra 路由字段，不导出提示词、回答或凭据。

已验证：三个 Lyra 目标与 gpt-5.6-luna（`--reasoning-effort high` 序列化为请求体 `reasoning_effort`）均能完成单题。推理模型的 `max_tokens` 含推理 token，2048 会被推理耗尽而零分；按设计取 IFEval 8192、GPQA 16384。inspect_evals 未提供 LiveCodeBench release_v6，改由仓库内 `inspect_tasks/livecodebench_v6.py` 薄包装接入：数据集读冻结题库并在任务 metadata 记录 pack 摘要，提示词与 ACP 路径同源（共用 `livecodebench_prompt`），solver 只做单轮生成、无工具无重试，评分复用既有断网 Docker grader 的 pass@1；grader 侧基础设施失败（Docker 缺失、超时、输出非法）作为 sample error 上报，不计为模型答错。该任务通过 `PYTHONPATH` 进入独立环境，不安装本项目，避免同名 `instruction_following_eval` 遮蔽 IFEval 评分器依赖的分支。IFEval 评分器首次运行需下载 NLTK punkt 数据，离线环境需预先放置。

HLE、BrowseComp、DRACO、FRAMES 不进入本轮建设范围；既有插件和历史协议保留，不删除。

## 判断前提

本地 Console 与并行调度的完整设计及当前实现边界见 [[local-console-execution-design]]。

### Docker + ACP 执行边界（拟议部署）

- 每个 Episode 使用独立的 Docker 工作目录、会话与可写层。容器运行选定的 Codex ACP 或 Claude Agent ACP adapter 与冻结运行时；模型推理仍在 APIGO Gateway 后端，不在容器部署模型权重。
- [claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp) 是 Claude Agent SDK 的 ACP 适配器，不是可以与任意 Claude Code CLI 版本等同的独立“官方 ACP 版”。manifest 分别记录 adapter、SDK、实际捆绑运行时和镜像 digest。ACP 只负责会话控制，不解决非 Claude 模型兼容或网络隔离。
- lyra-evals 在容器外作为 ACP client，通过非 TTY 的 stdio/JSON-RPC 管理会话、取消和事件。容器生命周期由受信任 Runner 管理，不向任务容器暴露 Docker socket。
- 任务容器的业务网络目的地仅允许 APIGO Gateway。优选固定上游的专用出口转发器：任务网络无通用公网路由，转发器只能连接 Gateway；禁止任意 CONNECT、重定向到其他目的地和动态客户端指定上游。网关凭据可由转发器注入，按 Episode 限预算、目标与 API 路径。
- 仅设置 BASE_URL、代理变量或 Docker bridge 网络不构成出站隔离。根据实际 Linux VM / Docker 防火墙后端落实默认拒绝，覆盖 IPv4/IPv6、直连、UDP/DNS、宿主和其他容器；必须验证不能绕过出口。Docker 网络规则以[官方防火墙说明](https://docs.docker.com/engine/network/packet-filtering-firewalls/)及部署后端为准。
- 构建/准备阶段下载并冻结 npm/Python 依赖、任务仓库、数据与工具；正式执行禁止联网安装与自动更新。每题只挂载允许的输入，不挂载宿主用户目录、项目外凭据、答案或隐藏测试。代码执行容器限制 CPU、内存、PID、时长，采用非特权用户且不授予修改网络规则的能力。
- ACP client 如果提供文件/终端回调，必须在对应 Episode 沙箱执行，不能在 Runner 宿主执行。权限批准不等于取消沙箱。τ²-bench 领域工具通过受控 stdio/IPC 桥执行，用户模拟器和隐藏状态由外部控制器管理；无需新增任务容器的公网目的地。
- 评分隔离：IFEval/GPQA 答案仅评分侧可见；LiveCodeBench 隐藏测试在独立离线 grader 运行；SWE-bench 从最终 patch 在新环境执行官方验证；τ²-bench 从受控环境状态评分。Agent 可运行被允许的可见测试，但不能读写官方隐藏评分环境。
- 审计/结算与 Judge/用户模拟器请求由外部控制器执行，使用独立角色凭据并关联 Episode。任务容器不能为对账而获得 Platform 管理 API 的访问权。
- 验收：Gateway 正常可达；其他域名/直接 IP/IPv6/宿主端口不可达；ACP 工具无法读写宿主；新 Episode 无旧状态；权限内的本地工具可用；隐藏测试不可见；全部模型调用有目标身份和账单关联。本段尚未部署或验证，不宣称隔离已成立。

- 用户已确认：Fusion 没有内置 `web_search`。这不等于 Fusion 不支持 function calling；后者及多轮工具结果回传仍待验证。
- 以下“已接入”指代码、manifest、数据/评分协议的集成状态，不表示本次已下载数据或完成线上 Fusion 实测。
- `verified` 是仓库 manifest 的实现标记，不代表目标可调用、费用完整、外部榜单可比或官方 Agent 已接入。
- 全部 benchmark 的 Fusion/单模型横向运行仍受公共缺口影响：Target/profile 策略、审计适配、完整费用、延迟及多目标比较尚需扩展。

## 当前仓库：6 个插件

| Benchmark | 测什么 | 仓库接入状态 | 官方 Agent / 执行流程来源 | 当前路径是否需要搜索 | Fusion 当前适配判断 | 下一步与优先级 |
|---|---|---|---|---|---|---|
| IFEval | 指令遵循 | `verified`；541 题清单与官方 evaluator 已冻结，评分源码已 vendor | [Google 官方数据与规则评分器](https://github.com/google-research/google-research/tree/master/instruction_following_eval)；无需工具 Agent | 否 | 无搜索障碍；仍需完成公共 Target/审计适配，不等于已实测 | P0：第一个横向闭环；同时报整题 strict 与指令 strict |
| GPQA Diamond | 高难科学选择题 | `scaffold`；198 题目标清单；缺授权数据工件哈希与选项打乱协议 | [作者 baseline 与提示流程](https://github.com/idavidrein/gpqa)；首版选 closed-book，作者另有检索 baseline | 否，当前选择闭卷轨 | 无搜索障碍；数据与协议未就绪 | P1：确认数据使用来源、冻结工件与 shuffle seed，补正式评分链路 |
| HLE Text-Derived | 高难综合知识与推理 | `scaffold`；纯文本派生子集未冻结，不应使用官方 2500 题作为本地子集题数 | [作者评测代码](https://github.com/centerforaisafety/hle)；采用明确的无工具文本轨，非完整多模态 HLE | 否，按当前无工具轨 | 无搜索障碍；数据、子集和正式评分协议尚未完成 | P2：冻结文本 IDs 与 scorer；只声明派生子集成绩 |
| BrowseComp | 网页查证与困难信息检索 | `verified`；1266 题清单及答案评判已接入；标为 `same_dataset_reference_only` | [作者 simple-evals](https://github.com/openai/simple-evals/blob/main/browsecomp_eval.py) 是评测/评分入口；不能据此认定已提供并接入完整搜索 Agent | 是；当前传 `web_search_options` | 当前执行路径不适配；没有外部搜索/抓取循环 | P2：先核实可复用参考执行器及 Fusion tool calling，再接外部工具；保持参考级口径 |
| DRACO | 长报告与多标准研究质量 | `verified`；100 题/3934 criteria 清单，rubric Judge 已接入；有工具契约 blocker | 现有参考是 [OpenRouter 移植 harness](https://github.com/OpenRouterTeam/benchmark-harness)，不是 DRACO 数据集作者实现；目前未接通该工具循环 | 是；参考涉及搜索、抓取及执行环境 | 当前执行路径不适配；提示中写工具预算不会产生真实工具能力 | P2：单独标记 OpenRouter 协议，核验固定版本的工具与循环；保留 `protocol_experiment_only` |
| FRAMES Development | 多跳检索与答案准确率 | `verified`；824 题清单，当前 dev/holdout 各 100 题；`internal_development_only` | [Google 数据卡](https://huggingface.co/datasets/google/frames-benchmark) 提供任务及来源；当前仓库采用内部 live-web 流程，未证明接入官方可运行 Agent | 是，指当前 live-web 配置 | 当前执行路径不适配；数据集本身不等于搜索服务 | P2：明确外部检索流程；若提供固定文档上下文，另建协议并标记 derived，不能冒充 live-web |

本地证据：`benchmarks/<name>/benchmark.yaml`、对应 `plugin.py`、`src/vohu_evals/benchmark.py` 和 `src/vohu_evals/gateway.py`。README 对部分赛道的说明滞后，盘点以 manifest 与实际调用实现为准。此处未修改历史 manifest 或执行逻辑。

## 新增赛道的官方来源与剩余工作

下表保留官方来源与完整执行所需条件；最新代码状态见上方增量说明，不代表已批准付费运行。

| Benchmark | 价值 | 可复用部分 | 需要内置 web_search 吗 | 接入前置条件 | 建议 |
|---|---|---|---|---|---|
| [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) | 代码生成质量与成本 | 官方生成/评测流程与执行测试；基础代码生成轨不要求多轮 Agent | 否；数据和依赖准备联网不等于模型搜索 | 固定版本/题目时间范围、隔离代码执行、运行限制、APIGO 模型适配 | P1：作为代码能力首选候选 |
| [τ²-bench](https://github.com/sierra-research/tau2-bench) | 多轮工具调用、业务任务完成 | 作者 Agent/用户模拟器、领域工具环境和 evaluator | 不是通用网络搜索任务 | Fusion 多轮 tools 能力、模型后端适配、用户模拟器固定与单独计费、环境重置 | P1：若 tool calling 验证通过，作为首个官方 Agent 接入试点 |
| [SWE-bench](https://www.swebench.com/SWE-bench/) | 真实仓库问题修复 | 官方 Docker 评测 harness；用于验证 patch，不等于完整 Coding Agent | 核心 patch 评测不要求内置搜索 | 另选并冻结公开 Coding Agent、沙箱与依赖、APIGO 接口兼容；计入更长任务链路 | P2：先明确 Agent 来源，再做工程任务评测 |

## 没有内置搜索时的接入方式

| Fusion 能力 | 可接入方式 | 结论边界 |
|---|---|---|
| 支持多轮 messages、tools、tool_calls 和工具结果回传 | 参考 Agent 在外部执行搜索/抓取，Fusion 只负责产生决策与回答 | 可以评测“该参考 Agent + Fusion”，不要求模型内置搜索 |
| 不支持原生 tool calling，但参考实现明确支持文本工具协议 | 严格复用该协议并核验解析、工具结果及停止语义 | 必须标明协议；新增自定义解析/修复时形成独立实验条件 |
| 只支持文本输入输出，参考 Agent 也不支持文本工具协议 | 先测无工具轨；或另建固定检索上下文的派生轨 | 不能称为原始联网 Agent 评测 |

目前 `Benchmark.build_request()` 把 manifest 的 `web_search: true` 转成请求标志，`APIGOGatewayAdapter.invoke()` 再发送 `web_search_options: {}`；它没有提供外部 `web_search` 工具实现。仅删掉这个字段会让模型闭卷答题，不能修复原研究协议。

## 公共接入检查表

| 检查项 | 当前结论 | 接入完成标准 |
|---|---|---|
| Fusion 原生搜索 | 用户确认没有 | 不再依赖 Fusion 原生搜索；需要搜索的轨由参考环境提供 |
| Fusion function calling | 待验证 | 声明 tools、返回合法 tool_calls、保留调用 ID、接收 tool 消息、多轮完成与停止；至少离线契约及后续显式预算 smoke |
| 官方 Agent 来源 | 不能统一假设存在 | 记录作者/维护者、仓库、commit、入口、参数；第三方移植单列 |
| APIGO 接入 | 当前主要是 VOHU 专用路径 | 在参考流程内仅替换模型 adapter，不绕过 Gateway；目标模型和 Judge 分开记录 |
| 外部工具 | 当前 evals 无统一研究工具循环 | 所有目标同一工具定义、服务、凭据权限、预算和错误语义 |
| 成本 | 现有逐题费用不足以覆盖所有路径 | 外层 Agent 多次调用、Fusion 内部收费、外部搜索/抓取、重试均关联任务；顶层和子项不重计；Judge/用户模拟器费用单列 |
| 耗时 | 当前主要记录单次 HTTP | 记录完整任务开始至完成，包含工具和重试；排除事后评分与结算等待 |
| 数据/评分 | 部分已冻结，部分 scaffold | 工件哈希、子集、评分器、Judge 协议完整；评分器与被测 Agent 隔离 |
| 可比性 | 各赛道限制不同 | 官方执行流程、题集、工具、预算等匹配后才评估官方可比性；APIGO 内部同协议比较单独声明 |

## 当前实施顺序

1. 按 [[model-effort-evaluation-design]] 实现能力契约、变体与离线计划；同步预算账本、Gateway 单出口门禁。
2. 接 Codex/Claude ACP driver 与原生 effort 映射；真实协议预检需显式预算。
3. 以 IFEval 接通回答提取、官方评分和完整费用，再接 GPQA closed-book、LiveCodeBench 生成轨。
4. 完成初筛与尖端模型 effort 深测，在保留集确认；Console 按模型、Harness、effort、预算分组报告。
5. 接 τ²-bench 用户/工具桥和 SWE-bench 编码沙箱，保留官方环境评分。

## 相关页面

- [[index]]
- [[benchmark-integration]]
- [[fusion-router-evaluation-design]]
