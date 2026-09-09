---
title: Fusion Router 横向评测设计提案
tags: [architecture, evaluation, proposal]
links: [vohu-evals-architecture, scoring-evidence, index]
updated: 2026-09-09
sources: 10
---

# Fusion Router 横向评测设计提案

状态：设计提案，未实现。当前实现事实与拟议能力分别说明；本文不改变现有运行策略，不包含实测成绩。

当前用户决策：范围为 IFEval、GPQA、LiveCodeBench、τ²-bench、SWE-bench，保留官方任务、环境与评分；GPT 使用 Codex ACP，Claude 使用 Claude Agent ACP，其他目标按已验证能力选择。模型 × Harness × effort × 预算构成实验变体。最新矩阵和可比性以 [[model-effort-evaluation-design]] 为准，取代此前统一 Claude Code 的决定。Fusion 无内置 web_search；非 Claude/Fusion 兼容性仍待验证。下文其他题库/框架仅为调研记录。

## 目标与结论

目标是在相同任务和可比条件下，通过 APIGO Gateway 比较 Fusion 融合 Router 与 APIGO 可用单模型的任务质量、用户实际成本和端到端耗时，回答“在给定质量和延迟要求下，哪个目标最划算”。

现有项目可复用，但不能直接满足目标。推荐保留 Benchmark plugin、不可变 manifest、SQLite ledger、恢复与审计链路，增补多目标实验、完整计费事件、延迟观测、配对统计和比较报告。第一阶段不整体迁移框架。复杂 agent/tool 任务再试点 Inspect AI 集成。

这里将 Fusion 视为 APIGO 暴露的被测系统目标，并不假设当前 VOHU 标识就是未来 Fusion 的正式标识。正式目标、策略版本、模型目录和价格必须在执行前从受控配置及实际服务冻结；本次没有调用线上模型目录或开展付费实测。

## 当前代码能证明什么

| 能力 | 实现证据 | 对新需求的影响 |
|---|---|---|
| 可复现运行 | `cli.py`、`ledger.py` 冻结 manifest，支持 trial、并发、恢复 | 可保留，新增多目标 experiment manifest |
| 目标模型 | `_profile_policy` 要求 composition，且 expected models 必须在中国模型 allowlist 内 | 普通单模型不能作为独立 baseline 正常接入；HTTP adapter 接受模型字符串不等于端到端支持 |
| 审计 | `audit.py` 强依赖 `vohu_execution`、attempts 和 settlement | 需要单模型和 Fusion 两类审计适配 |
| 成本 | `InvocationResult.cost_usd`、case 账本和总费用汇总 | 只有 case 级结果不足以完整核算失败、客户端重试和融合内部调用 |
| 耗时 | `gateway.py` 用 monotonic 记录非流式 HTTP 请求耗时 | 不含之前的重试等待；无 TTFT、分位数、任务 SLA 汇总 |
| 质量 | Benchmark plugin 与固定分母，IFEval 官方 evaluator | 可保留，但通用报告不能把所有 rubric 分数都解释成准确率 |
| 横向报告 | `reporting.py` 有 evidence 和外部参考；多 trial aggregate 依赖 IFEval 字段 | 不是通用的同题多模型比较引擎；缺配对差值、置信区间与 Pareto 报告 |
| 输入协议 | BenchmarkRequest 主要是 prompt、搜索开关、timeout | 缺多轮 messages、工具 schema、生成参数冻结等能力 |

必须先修复的统计问题：

1. `runner.py` 对空解析输出调用 `ledger.fail` 时不传 result，已发生请求的费用和耗时不能完整保存；Gateway 错误路径也没有完整费用事件。`request_attempts` 当前只记录预留信息，不能汇总每次客户端调用的真实账单。不能据此推断失败免费。
2. `audit.py` 在允许未结算时返回费用零和 `cost_settled=False`，但该状态未进入 InvocationResult 和逐题账本。改为金额可空、显式状态；未结算、已结算零费用必须可区分。
3. 美元预算按已落账 case 总费用检查，无法预留并发在途请求和未知费用；只能视为尽力限制。需要预留额度与 Gateway 硬限额配合。
4. JudgeResult 没有已结算费用字段。评分调用的 usage 不等于完整费用核算，须单列评测支出。
5. 报告发布条件不能仅看题目全部 completed；还要验证计费覆盖、可比协议及数据集级资格。汇总不得把未满足资格的输入升级成可发布。

Benchmark 的实际状态以 manifest 为准：IFEval verified；GPQA/HLE 仍 scaffold；DRACO verified 但有工具协议比较 blocker；FRAMES 只用于内部开发；BrowseComp 标注 same_dataset_reference_only。README 对 DRACO 的描述已落后于 manifest，不能据 README 认定所有研究赛道仍未接入，也不能把 verified 等同于可与官方榜单直接比较。

## 常用框架调研与选择

以下为官方文档和原始项目所述能力；“采用方式”是本提案的工程判断。

| 项目 | 适用能力 | 本项目采用方式 |
|---|---|---|
| [Inspect AI](https://inspect.aisi.org.uk/) | Dataset、Solver、Scorer、工具、sandbox，支持细粒度评测日志 | 后续复杂任务优先试点；通过 APIGO adapter 集成，账单以 APIGO 结算为准 |
| [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) | 标准学术任务与可复用评测定义 | 借用已冻结任务与 scorer；注意 [API guide](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/docs/API_guide.md) 中 Chat Completions 对 loglikelihood 任务的限制，不能假设所有题库即接即用 |
| [OpenCompass](https://github.com/open-compass/opencompass) | 多数据集、多模型和 API 模型评测 | 用于扩展中英文标准任务的参考；第一阶段不引入第二套完整调度系统 |
| [HELM](https://arxiv.org/abs/2211.09110) | 多场景、多指标评测，包含效率维度 | 借鉴场景分层与统一协议，不把所有能力压成单一总分 |
| [Promptfoo](https://www.promptfoo.dev/docs/configuration/outputs/) | 应用断言、结果导出，输出 latency 与估算 cost | 适合应用回归和结果浏览；估算价格不能替代 Fusion 内部调用实际账单 |
| [RouteLLM](https://github.com/lm-sys/RouteLLM) | 强弱模型路由、阈值校准、随机基线及路由评测 | 借鉴阈值扫描、质量—成本曲线和随机基线；其双模型历史缓存不能证明当前 APIGO/Fusion 的实测效果 |

Inspect 的[日志与恢复机制](https://inspect.aisi.org.uk/eval-logs.html)可作为后续适配参考，但应只有一个组件负责客户端重试和预算，避免与现有 Runner 叠加。框架替换不会自动解决 APIGO 结算、内部 fan-out 和公平对照问题。

## 实验设计

实验单位为 `case × target × trial`。同一 experiment 冻结 case IDs、数据及 scorer 哈希、提示协议、生成参数、工具权限、目标版本、价格快照、缓存策略、客户端重试、超时、负载和执行顺序种子。

### 对照组

- APIGO 单模型：先覆盖用户常用模型和不同价格/能力档位，再扩展目录中所有符合赛道能力的模型。逐项记录不支持或不可用原因。
- Fusion：每个公开策略版本作为独立 target；若产品支持成本、均衡、质量档位则分别测，不臆造 API 模式。
- 路由基线：固定便宜模型、固定强模型、随机路由、简单规则路由。全部使用同一候选池；随机路由要有匹配路由占比的对照。
- 消融：在服务确实支持时比较 router-only、增加 fallback、完整 fusion，判断增益来自选模、重试还是融合。
- 离线 oracle：用独立测试结果分析“每题最低成本可答对模型”的选择上界；标注 hindsight，不作为可部署模型。选模 oracle 也不是融合生成能力的理论上界。

应分清三个评测层次：端到端产品效果是主结果；路由选择诊断用于找改进点；消融用于归因。不能只比较 Router 选对模型的比例来代替最终任务成绩。

### 可比条件

- 同题、同可见上下文、同工具能力、同评分规则。Fusion 内部额外推理与多模型调用可以存在，但必须计入全部成本和用户等待时间。
- 固定协议轨用于公平对照；生产默认轨用于真实产品体验。不同轨单独报告，不将完全相同 temperature 视为所有模型都必然支持的要求；不支持的参数显式标记。
- 统一客户端重试和外层任务 deadline。产品内部重试是被测行为，按真实发生情况保留；答案错误不得为了提升成绩而重试。
- 首先以低负载测单任务延迟，再单独进行明确并发或到达率的负载实验。相同并发不等于相同 QPS。
- 按 case/block 随机交错目标顺序，限制共享配额干扰；记录地区、时段、限流及缓存命中。冷缓存和生产缓存分开。
- 开发/校准集用于调路由阈值；冻结独立 holdout 作最终比较，防止路由训练或反复调参污染。不能把选出的最佳阈值在同一测试集上的结果当作无偏估计。
- API 模型版本无法完全固定时记录别名与观测版本，降低可复现性声明。APIGO 实际目录与协议兼容性应 preflight，不能硬编码模型名单。

## 指标口径

### 任务质量

二值任务报告 `正确任务数 / 冻结任务数`；系统失败、超时、无效输出保留在分母。另报可成功交付请求的条件准确率和服务可用率，定位模型与系统问题；预算中断的实验标记 incomplete，不能当作完整模型榜单。

IFEval 同时报 prompt-level strict 和 instruction-level strict，默认 manifest 的 instruction 指标不等于完整任务通过率。代码报告 pass@1；有 rubric 的任务报告归一化 rubric 分数。自由文本评判冻结 judge、rubric 与版本，隐藏 target 身份，抽样人工复核，pairwise 评价交换答案顺序检查位置偏差。

每个赛道单独报告。只有事先固定业务任务比例时，才生成带权业务汇总，不平均不兼容的原始指标。对 Fusion 与各 baseline 计算同题质量差值、成本差值及置信区间；按 case 做配对 bootstrap，多 trial 时按 case 聚类，不能将同题重复视为独立样本。比较很多模型时标注多重比较处理方式。

### 成本

用户成本为主指标；供应商原始成本作为内部诊断，两者不混用。冻结币种、价格有效时间、折扣、缓存、输入/输出/reasoning token 和工具计费规则。金额用 Decimal 或整数最小单位，不以 float 作为最终账本权威。

`任务用户成本 = 与该逻辑任务关联的所有已结算客户收费事件之和`。

涵盖客户端重试、Router 判定、专家/候选生成、verifier/finalizer、fallback、搜索等实际收费。每个收费事件按唯一 ID 去重；如果顶层任务账单已包括子调用，子项只作分解，不再次加总。对未收费的内部调用也记录 usage，不自行假设它对应客户收费。

主报表：总支出、每任务均费、每千任务费用、成本 p50/p95、每正确任务成本，以及相对各 baseline 的节省率。`每正确任务成本 = 所有任务成本 / 正确任务数`，分子包括失败和错误请求，零正确任务显示不可用。

`节省率 = 1 - Fusion 同题平均用户成本 / baseline 同题平均用户成本`。baseline 为零则不可计算；费用未完整结算时不发布精确节省率。估算、部分结算和实际结算分栏，并展示覆盖率。

另列评测运营成本：被测调用 + judge 调用 + 数据处理/沙箱等已计入项目开销。Judge 成本进入 campaign 总预算，但不能混进产品服务单次任务成本。

### 耗时与可靠性

- E2E：逻辑任务首次发送至最后完成或终止，包括网络、Gateway 排队、内部调用、客户端 retry/backoff；不含事后 judge 与结算轮询。
- TTFT：流式首个用户可见内容 token；心跳、角色事件或隐藏 reasoning 不算。当前非流式结果不能补算 TTFT。
- 报告成功 E2E 的 p50/p90/p95，失败终止耗时分布、超时率、限流率、重试率及样本数；足够大样本再报 p99。
- 超时是右删失观察，不能静默丢掉后宣称更快；联合指标 `正确且在 SLA 内完成的任务数 / 全部任务数` 用于衡量实际交付。
- Fusion 并行步骤耗时看关键路径，不能相加当作用户延迟；步骤总耗时可单列为资源诊断。

### 决策输出

主表列出 target、赛道分数及区间、每千任务费用、每正确任务成本、E2E p50/p95、SLA 内正确率、失败率与费用覆盖率。附成本—质量散点图和质量—延迟图，标注不被其他目标同时在质量、成本和延迟支配的 Pareto 候选。

最终问题是：满足质量非劣界限与延迟 SLA 时的最低成本。非劣界限、SLA 与业务权重须事先确定。若差异证据不足，结论为“不足以证明更优”，不能用微小点估计差异宣传胜出。

## 题库组合

| 层次 | 推荐任务 | 用途与限制 |
|---|---|---|
| MVP | 现有 IFEval + 脱敏业务任务（分类、抽取、JSON、问答） | 先跑通同题横向链路；便宜任务是检验路由额外开销的必要样本 |
| 推理 | 数学/知识任务，现有 GPQA 在授权和协议完整后接入 | 包含难度梯度，不只挑最难题；Chat API 使用生成式答案评分 |
| 代码 | [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) 的冻结版本 | 官方项目以代码能力评测为中心；答案在隔离沙箱执行，报告 pass@1 |
| 长上下文 | [LongBench](https://github.com/THUDM/LongBench) 的冻结任务与实际长文抽取 | 按长度分桶，记录截断；超出能力范围单列，不静默截短后混比 |
| 工具与研究 | 现有 BrowseComp、FRAMES、DRACO | 必须匹配工具环境；当前参考/内部限制保持，先作为独立研究轨 |

IFEval 官方定义和评分器见 [Google Research](https://github.com/google-research/google-research/tree/master/instruction_following_eval)。私有业务题库比扩大公开榜单更能回答 APIGO 用户的真实 ROI；中英文分别分层，权重来自实际目标流量。

初始校准可用几百道分层题验证成本和方差；正式样本数依预先要求的最小可检测质量差确定。不要用几十道题证明 1 个百分点的改进。简单独立二项近似下，单一准确率最坏情况的 95% 区间半宽约为 `0.98/sqrt(N)`；500 题约 4.4 个百分点，这不是配对差值的功效计算。

## 拟议模块与数据契约

```text
Experiment manifest（cases × targets × trials、协议、预算、价格快照）
  → Scheduler → APIGO Target Adapter → 单模型 / Fusion endpoint
                       ↓ response、时间事件、关联 ID
                 Invocation ledger ← Audit / Billing reconciliation
                       ↓                 ↓
                 Scorer plugins → Paired comparator → Evidence / Report
```

- TargetSpec：kind（single/fusion）、API model ID、可用协议与能力、版本、策略快照哈希。旧 VOHU composition 约束成为特定 target policy，独立单模型只校验冻结目标和 Gateway 审计。新的项目范围需同步更新 AGENTS.md 与 README，不能通过关闭 audit 来绕过限制。
- ExperimentSpec：冻结目标矩阵、题集、trial、参数、deadline、负载、分层权重与比较规则，分发到可恢复子 run。
- InvocationEvent：每次外层请求的独立 ID、case/target/trial、request/execution ID、起止时间、首内容时间、状态、usage 与错误；在解析/评分之前持久化。
- ExecutionSpan：可选内部步骤角色、模型、父子关系、时间、usage；后端未暴露时允许黑盒端到端评测，但不得声称完成内部成本归因。
- BillingEvent：结算事件 ID、对应调用、客户/原始金额、价格版本、状态与包含关系；失败、超时、断线后仍通过关联 ID 对账，缺关联 ID 标为未知。
- ScoreRecord：独立于执行结果保存 scorer 版本、值、单位、judge 关联调用；重评分不重复收取目标调用成本。
- Evidence：数据完整性、协议可比性、费用覆盖率和发布资格分别判定，最终采用 `comparable`、`reference_only`、`not_comparable`；内部 APIGO 同协议可比不代表与官方公开榜单可比。

暂不要求统一所有 agent runtime。若引入 Inspect，先验证同一小题集与现有 scorer 结果一致，且账单、重试、deadline 和请求 ID 可完整映射，再扩展。

## 实施顺序与验收

1. P0：更新项目范围；增加 single/fusion TargetSpec 和按类型审计；建立 request/billing 账本，修正失败费用、未结算状态和预算在途额度。验收：错误响应、空输出、超时重试、延迟结算、重复结算事件均不漏计/重计；未知不显示零。
2. P1：以 IFEval 和业务题集实现同题多目标 campaign，输出质量、成本、E2E 与配对差值。先低负载，预先冻结目标和协议。验收：缺题、协议不一致、价格不完整会阻止完整可比声明；恢复和重新评分不修改原始证据。
3. P2：加入阈值扫描、随机/规则基线、消融、独立 holdout、Pareto 与非劣分析。验收：可回答“达到预设质量及 SLA，Fusion 相比各 baseline 的成本变化及不确定性”。
4. P3：流式 TTFT、负载曲线、长上下文/代码/工具轨，以及 Inspect 可行性试点。是否启用每一项取决于业务需求和 Gateway 实际协议能力。

付费执行前输出请求量与预算计划：`题数 × 目标数 × trials` 只是外层请求基数，还要预估 Fusion 内部调用、重试和 judge。预算需覆盖所有评测支出并保留在途额度。当前提案未指定或批准任何真实调用额度。

## 相关页面

- [[vohu-evals-architecture]]
- [[scoring-evidence]]
- [[index]]
