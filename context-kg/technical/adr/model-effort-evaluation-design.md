---
title: 模型与 Effort 实验矩阵设计
tags: [architecture, evaluation, proposal]
links: [index, local-console-execution-design, benchmark-onboarding-checklist, fusion-router-evaluation-design]
updated: 2026-09-09
sources: 0
---

# 模型与 Effort 实验矩阵设计

## 决策与实现边界

本页是当前评测方案的增量权威，取代此前所有模型统一 Claude Agent 的决定。GPT 系列使用冻结版本的 Codex ACP；Claude 系列使用冻结版本的 Claude Agent ACP；其他模型与 Fusion 依据已验证的协议和工具能力选择 Harness，不静默切换。所有推理仍通过 APIGO Gateway，Fusion 无内置 web_search。

本页包含已实现的离线计划层和待实现的正式执行层。当前已实现多 effort 变体去重、规模限制、不可变计划持久化/导出、Console 矩阵预览、能力未验证状态，以及独立预算账本的原子预留/幂等结算。Codex 配置生成与 ACP effort 协商已提供；尚未接入真实 Codex 任务调度、Gateway 预算对账或正式结果曲线。五个赛道和官方评分的既有接入状态见 [[benchmark-onboarding-checklist]]，不能从模型目录可见推断模型可执行。

模型 ID、报价快照、账户凭据和实际目标组合保存在本地运行输入；知识库仅保存选择原则与契约。既有本地成本表用于选型，不作为结算真值。

## 三类问题与可比性

| 实验轨 | 控制条件 | 回答的问题 | 可比性 |
|---|---|---|---|
| 同 Harness 固定模型与 Fusion | 同题、工具、提示适配规则、预算、Harness 版本与 profile | 路由是否改善质量、成本或延迟 | 条件匹配且证据完整时 comparable |
| 模型＋各自 Harness | GPT/Codex 与其他模型/Claude Agent 等显式组合 | 用户可获得的完整系统效果 | 模型归因 reference_only；可比较已明确声明的系统指标 |
| 同模型 effort 扫描 | 同 Harness、题目、工具、其他采样参数，effort 为主要变量 | 更多推理是否值得 | 条件匹配时 comparable；不将不同家族同名 effort 视为等算力 |

官方榜单可比性另设字段逐赛道判断，内部 comparable 不自动代表官方 comparable。题集、评分协议或工具权限无法匹配时 not_comparable。禁止将三个实验轨混为一个模型排行榜。

## 模型选择与执行阶段

首轮基线覆盖 GPT 的低/中/高价格档、Claude 的中/高档、同家族低成本/进阶模型及另一家低成本模型；Fusion 覆盖默认、成本、质量策略，延迟策略按目标加入。其他家族在能力验证后扩展。选择依据是成本梯度和家族覆盖，不是尚未实测的质量排名。

| 阶段 | 输入与控制 | 输出与退出条件 |
|---|---|---|
| P0 离线计划 | 目录及价格快照、冻结能力声明、合成响应 | 去重后的矩阵、费用区间、阻塞列表；不调用模型 |
| P1 协议预检 | 显式获批的小额预算；每个目标/effort 验证请求、流式、工具、usage | unsupported、未验证、通过分别记录；只开放已通过变体 |
| P2 初筛 | 全候选显式基线档；固定开发集、同题配对与交错顺序 | 淘汰明显不兼容方案，选定深测名单与预算；不作为最终成绩 |
| P3 深测 | 尖端模型、Fusion 及初筛优秀候选；各自支持的 effort 档 | 成本—准确率与耗时—准确率曲线，确定待确认配置 |
| P4 确认 | 在未用于挑选配置的冻结保留集运行；预先固定配置、trial 和预算 | 可发布内部报告，保留全部失败、超时、支出与置信区间 |

首版每个候选深测最多选择 3 个不同有效档：基线、较高档、最高档；基线与最高档相同则去重。需要研究低 effort 时追加独立配置。不对所有模型盲目笛卡尔积，也不保证所有模型恰好有三个档。扩展矩阵必须重新估算并确认运行预算。

IFEval/GPQA/LiveCodeBench 先接通无工具轨；τ²-bench 与 SWE-bench 在工具、环境和独立评分完成后开放。trial 数按先导方差、预算及协议预先决定，不能不断重跑直到显著；重复 trial 不能当独立题目扩充样本量。

## 配置与不可变数据契约

连接凭据与实验变体分离，多个 effort 复用同一连接；历史 run 不随连接或能力目录变更而变化。

| 实体 | 拟议字段与约束 |
|---|---|
| ModelConnection | id、endpoint、model、protocol、secret_ref；只存连接和脱敏凭据引用 |
| CapabilitySnapshot | model、protocol、Harness/adapter 版本、支持的原生参数与合法值、来源、校验时间、验证状态、证据引用 |
| EvaluationVariant | connection_ref、harness、profile_hash、effort_mode、effort_native、参数映射版本、输出上限、deadline、费用上限、重试规则 |
| ExperimentManifest | schema_version、变体快照/hash、数据/评分器/hash、case IDs、trial、seed、顺序、并发、缓存策略、价格快照、总预算 |
| RequestEvidence | requested_effort、serialized_effort、effective_effort、effective_source、实际模型、角色、request_id、usage、时间、重试关联 |
| FusionTrace | 路由策略版本、请求 effort、下游模型/effort、映射原因、内部 request 关联；未提供字段显式 unknown |

Effort 模式区分 explicit、provider_default、unsupported。正式 effort 曲线要求 explicit 且原生值合法；无法固定的默认档单独标为 provider_default，不伪装成 medium。不同模型的 effort 枚举或 thinking token budget 由各自 adapter 映射，不能共享未经验证的枚举。输出 token 上限与 thinking budget 是不同约束。

serialized_effort 表示出口实际发送的参数，不等于提供商真正执行的 effort。只有可信响应或 Gateway 审计可提供 effective_effort；否则为 unknown，不能把请求值复制过去充当证明。未确认生效的条件保留结果但降为参考，不进入已验证 effort 排名。不存储或要求模型私有思维链。

变体 hash 覆盖所有影响执行的非秘密字段，秘密引用不进入导出。Job = benchmark × variant × trial；Episode = Job 的一个 case。适配参数、输出限制、Harness 或 profile 改变都产生新变体，不覆盖已有结果。辅助调用、压缩调用、fallback 的模型与 effort 同样记录；无法禁用的差异进入 manifest。

## 预算、公平性与统计

分别提供两种预算策略，不能混为一次对照：

1. 能力扫描：输出上限和 deadline 设置到先导实验表明较少截断的范围，并冻结；仍有总费用硬上限。报告预算耗尽、截断、超时占比，不将其误判为纯推理能力差异。
2. 等资源比较：所有目标采用相同的单题费用上限或同一 deadline，其他上限作为保护。报告给定预算下成功率；同名 effort 和相同 token 数不等于相同资源消耗。

费用分 estimated、reserved、pending、settled。用十进制金额和明确币种；价格版本、缓存、分时和长上下文档均进入估算。未知报价不能填零。所有在途调用先原子预留允许的最坏费用，超出实验/变体/单题预算则停止派发；拿不到有限上界或 Gateway 额度保障时拒绝正式运行。

分别报告服务成本与评测开销：服务成本包含所有成功/失败/重试的实际模型、路由和工具收费；Judge、用户模拟器及 Docker 等运营成本单列，并可合计总支出。顶层账单与内部子项按包含关系去重，按价格估算的费用不能标成已结算。推理 tokens 若已经包含在 output 中不可再加一次；缺失 usage 保留 unknown。

每成功任务费用 = 全部已执行任务服务成本 / 成功任务数；零成功时为不可计算，不显示零。未完成的计划同时报告覆盖率，不能只拿已完成题目对比。错误、超时、截断和系统失败保留在冻结分母中，另报原因。

耗时拆为排队、容器准备、Agent 执行（含工具/重试）、评分、结算；主任务延迟为派发至答案/最终工件完成，同时给出纯 Agent 执行耗时。p50/p95 附样本数，超时作为截尾/失败单列，不能只展示成功任务的快速子集。并发、限流、时间块、冷/热启动和缓存策略均冻结；按题目块交错目标顺序。

每个 benchmark 单独报告官方指标：IFEval 整题/指令 strict 与 loose；GPQA 选择题准确率；LiveCodeBench 生成轨 pass@1；τ² 按官方任务成功/多次试验指标；SWE 按冻结子集 resolved rate。跨赛道不直接平均成总分。

同题配对比较，按 case 聚类 bootstrap 给出 95% 区间（trial 留在 case 内），并报告差值；小样本明确不确定性。多配置初筛与保留集确认分开，避免在同一测试集选最高档再宣称泛化优势。绘制实际成本和耗时的 Pareto 前沿，不把无统计把握的微小差异标为胜出。

## Console 交互与 API 规划

保留 Paper/Ink 白底黑色强调主题、React/Vite/shadcn/Tailwind/Zod/Zustand/TanStack Query 技术栈。矩阵表单、预算策略、计划规模与阻塞预览、历史计划读取和导出已实现；真实执行记录与结果曲线待实现。

| 页面 | 交互与展示 |
|---|---|
| 模型连接 | 展示目录可见、协议声明、已预检三个独立状态；凭据永不回显 |
| 实验配置 | 选择 Harness、原生 effort、预算策略；不支持值禁用并说明原因；展示生效验证状态 |
| 计划预览 | 展开为去重 variant 列表；显示 Job/Episode 数、估算区间、已知/未知价格、总预留和阻塞原因 |
| 执行详情 | 按模型/effort 分组，显示 requested/serialized/effective effort、进度、在途预留、已结算费用及预算终止原因 |
| 对比 | 过滤赛道、Harness、profile、effort、预算；横轴费用或耗时，纵轴准确率；同模型档位连线，附区间、样本数及原始表格 |
| 报告 | 明示比较轨、官方可比性、覆盖率、未知账单、失败率、截断率和导出 manifest |

不同 effort 使用点形、线型与文字区分，不能只靠灰度。未知费用不落到坐标零点；不同 profile 不默认连线；未结算或未验证条件不进入正式 Pareto 前沿。

拟议 POST /api/plans 校验并展开矩阵，返回 manifest hash、规模、费用区间和 blockers；POST /api/runs 引用冻结 plan hash 与预算确认。执行时重新检查能力有效性和预算预留，条件变化应阻塞而非悄悄修改计划。新增只读 capabilities 查询和计划详情；API 与 Zod 同步版本化。

旧连接可复用，旧 run 按旧 schema 显示“未记录 effort/Harness”，不能补写推断值。现有不含新字段的真实运行请求在迁移后要求补齐计划；合成自检保留且不消费模型预算。

## 实施与验收清单

| 优先级 | 交付 | 通过标准 |
|---|---|---|
| P0 | 能力契约、变体与离线 Planner | 拒绝非法组合、去重矩阵、精确规模、hash 稳定、未知价阻塞；无付费请求 |
| P0 | 预算账本与 Gateway 单出口 | 并发预留不超额，迟到/重复账单不重计，旁路不可达，凭据不泄露 |
| P1 | Codex ACP 与 Claude 参数映射 | 固定镜像可离线握手；显式预算预检验证主/辅助调用、工具、effort 出口；不支持时不回退 |
| P1 | IFEval 完整闭环 | 独立回答提取、官方评分一致、任务/请求/费用关联完整；合成成绩不混入 |
| P1 | Console 矩阵配置与计划预览 | 前后端拒绝无效值、展示未知状态、取消保留账单、旧 run 不伪造新字段 |
| P2 | GPQA 与 LiveCodeBench、深测与报告 | 冻结数据和 seed、独立隐藏评分、配对区间、失败分母、费用曲线 |
| P3 | τ² 与 SWE | 用户工具桥、环境重置、patch 评分及隔离完成，模拟器费用独立 |

必要离线测试覆盖：非法 effort、静默忽略参数、同档去重、配置变化 hash、旧记录迁移、并发预算竞争、usage 重叠、超时已收费、延迟结算与取消、零成功费用、部分覆盖、不同 Harness 的可比性降级。真实预检只在获批预算内执行。

## 相关页面

- [[index]]
- [[local-console-execution-design]]
- [[benchmark-onboarding-checklist]]
- [[fusion-router-evaluation-design]]
