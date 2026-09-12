---
title: VOHU Evals Context-KG 变更日志
tags: [meta, log]
links: []
updated: 2026-09-12
sources: 0
---

# VOHU Evals Context-KG 变更日志

## [2026-09-12] decision | Lyra 协议实测与直连剖面复用 Inspect

- 逐协议实测：Lyra 三个路由只接受 OpenAI Chat Completions，Messages / Responses 返回 400 或 502；Console 中 Lyra 目标协议改为 `openai_chat_completions`。
- 直连 API 剖面不自写 harness，复用 Inspect AI `openai-api` 提供者与 inspect_evals 官方 `ifeval`、`gpqa_diamond`；新增独立环境包装脚本与日志汇总脚本。
- Lyra usage 含路由开销、响应 `lyra` 字段含路由证据与 execution id；推理模型输出上限须含推理 token。

## [2026-09-12] fix | GPQA 真实 ACP 从不出分：工具调用循环而非解析器

- 诊断真实 ACP 小样本验收里的 GPQA episode 证据：多起 `system_failed`（TimeoutError / episode budget exhausted / Agent 未正常完成回答）均在请求级 usage 里带有 `response.custom_tool_call_input.*` 事件，说明模型在几乎每轮响应都尝试发起工具调用，但该赛道不提供工具结果，Agent 因此反复重试、跨多次请求耗尽 deadline 或预算，从未产出可解析文本；唯一评出分的样本文本能被既有严格解析器正确抽取，只是答案本身错——解析器不是根因。
- 在 `suites/packs.py::gpqa_rows` 的 GPQA prompt 尾部追加显式闭卷/禁工具/单轮纯文本协议句，降低模型主动发起工具调用的倾向；`gpqa_score` 增加防御性 Markdown 修饰符剥离（`*`/`_`/`` ` ``），解析规则本身未放宽，仍是“显式最终答案行或独立字母行，取最后一条，含糊不计分”。
- 新增/扩充 `tests/test_suite_integrations.py`（真实观测到的收尾格式、含糊多答案取最后一条、无显式收尾不计分）与 `tests/test_gpqa_offline_scoring.py`（`scripts/score_benchmark.py` 端到端 fixture 覆盖，含用例覆盖不全时报错）。
- Deadline/`max_output_tokens`/episode 预算默认值不在本次改动范围（属于 planner 一侧），已按观测的耗时与 reasoning token 用量另行反馈，未在此处修改默认值。

## [2026-09-12] fix | Lyra 路由目标的 Anthropic Messages 入口

- Lyra 路由目标与直连模型共用 `/v1/messages`，但入口只接受协议子集，且各档位不一致；同一 harness 下直连 Claude 正常、三个 Lyra 目标全部 400。拒绝规则位于闭源组件，公开 Gateway 已移除 Lyra 路由，无法从代码确认参数名。
- 转发器对 `apigo/lyra-*` 目标只删除不影响采样结果的参数（`metadata`、`context_management`、`cache_control`），并把全文本 `system` 块按原序连接为字符串；`max_tokens`、`thinking`、消息内容原样透传，非文本 `system` 块直接拒绝而非改写。
- 账本逐请求记录已应用的规则名与无提示文本的出站请求形状，用于把 Gateway 拒绝归因到具体参数；规划阶段对该组合追加 blocker，要求先做单题入口预检。

## [2026-09-09] fix | ACP 闭卷校准

- 新模型未出现在 ACP effort 目录时，Codex 使用冻结配置文件，并由 relay 验证每次出站 effort；不声明供应商内部 effective effort。
- 闭卷任务在发送前移除 harness 工具声明，按实际出站请求大小预留预算，保持 agent 执行器不变。
- 缺少成功 Gateway 请求证据时拒绝评分，防止 ACP 错误文本被当作模型回答；历史无证据评分保留审计后失效。

## [2026-09-09] fix | 精确账单兼容

- 日志详情缺少精确金额时，从同时间窗列表读取并再次核验请求 ID、模型与结算状态，拒绝使用浮点展示金额替代。
- 工作区必须以实际请求归属验证，不能从登录令牌的组织字段直接推断。
- 增加详情字段缺失路径的回归与幂等验证。

## [2026-09-09] design | Fusion 路演实验

- 整理三个公开评测子集的主对照、次要 effort 对照与配对统计提案。
- 明确 Inspect direct-api 为待实现的新 profile，不混用现有 ACP 运行证据。
- 具体模型与运行计划保存在本地设计材料；未启动新付费调用。

## [2026-09-09] fix | IFEval 统计导出

- 汇总新增评分覆盖率、四项 IFEval 指标和 prompt 准确率 Wilson 区间。
- instruction 指标使用微平均；缺失或重复逐题证据不输出四项完整指标。
- 逐题导出增加 agent duration、strict/loose prompt 和 effort 序列化证据；未确认的 provider effort 保持未知。

## [2026-09-09] implementation | SWE Lite 子集

- 独立冻结官方 Lite test 300 题，保留 Verified 500 题和旧计划路径。
- Console 新计划默认 Lite；调度、环境准备与 CLI 评分按冻结子集读取，结果/导出显式标注 subset。
- 数据准备不等于真实模型评测；启动付费样本仍受已有授权预算约束。

## [2026-09-09] design | 做题并发与配额调度

- 根据用户给定 RPM 1,000 / TPM 2,000,000，设计 16 请求并发起步、32 为首轮上限和 80% 配额目标。
- 区分题目、API 请求和评分资源；统一 Key/Workspace 配额，不把模型连接数当作独立额度。
- 核对 Gateway quota probe 响应头及开关，明确尚无已确认的公开实时剩余额度接口；方案尚未实现。

## [2026-09-09] fix | 交互赛道、运行证据与账单同步

- 接入 τ² 独立 Gym / MCP 服务与固定模拟器预算，补齐 SWE 工作区、环境工具、补丁和断网官方评分。
- SWE 固定兼容旧 Verified 数据的官方 4.1.0，增加所选实例准备与付费前门禁。
- 修复流式使用量的增量解析、输出 token 参数边界和取消时的连接终止。
- 新增私密平台账单配置、请求级精确对账和幂等同步；缺失凭据或账单证据仍保持未知。
- 离线接入验收不等同于真实模型矩阵完成；各模型/effort 的能力验证与正式比较门禁继续保留。

## [2026-09-09] implementation | 结果总览与三赛道真实 ACP 调度

- 新增统一作业/逐题结果投影、筛选、CSV/JSON 导出与成本/时间/质量图。
- 三个赛道接入真实 ACP、每题独立 Gateway 转发器、价格上界预算与费用证据；未决账单保持为空。
- 真实验收发现并修复消息拼接、协议工具声明、终止事件用量持久化时序问题。
- τ²/SWE 的完整交互执行与平台账单对账仍待接通，未宣称全部完成。

## [2026-09-09] implementation | 五赛道数据与评分接口

- 新增数据准备、哈希校验、公开输入投影、离线评分 CLI 和官方包安装入口。
- 接入 GPQA 作者公开包、LCB 官方断网评分、SWE patch/报告格式及 τ² Gym 会话桥。
- Console 使用真实数据准备状态；计划冻结具体抽样 IDs。真实模型调用与全部端到端链路仍未开放。

## [2026-09-09] implementation | 离线矩阵计划与预算基础

- 实现 Planner 与 plans/capabilities API、内容寻址计划持久化、Console 多 effort 配置及导出。
- 添加独立预算账本，验证并发竞争、幂等与迟到结算；尚未接入 Gateway 真实计费。
- 添加 Codex 无凭据配置生成与严格 ACP effort 协商；真实执行继续关闭，不将候选值标为已支持。

## [2026-09-09] design | 模型与 Effort 实验矩阵

- 新增 [[model-effort-evaluation-design]]：GPT/Codex、Claude/Claude Agent、能力验证与原生 effort 映射。
- 明确同 Harness 路由对照、跨 Harness 系统参考、等资源比较及保留集确认。
- 同步 AGENTS、接入清单与 Console 设计；本次仅设计更新，尚未实现新配置或开启真实调用。

## [2026-09-09] implementation | 本地 Console 与 Docker/ACP 自检

- 新增 React/Vite 本地 Console、模型配置 API、持久化作业、SSE、并发自检与取消路径。
- 新增 [[local-console-execution-design]]，明确完整目标架构与当前自检交付的区别。
- 正式模型执行在数据/协议/隔离/计费完成前拒绝，不用合成成绩替代真实 benchmark。

## [2026-09-09] design | Docker 与 ACP 沙箱边界

- 在 [[benchmark-onboarding-checklist]] 补充每 Episode 容器、Claude Agent ACP adapter、Gateway 单一业务出口和独立评分边界。
- 明确 ACP 文件/终端回调必须进入任务沙箱，网络限制由受信任基础设施实施；尚未部署。

## [2026-09-09] decision | 五个评测集统一 Claude Code

- 用户选定 IFEval、GPQA、LiveCodeBench、τ²-bench、SWE-bench，执行 Agent 统一 Claude Code。
- 更新 [[benchmark-onboarding-checklist]] 与设计提案，记录赛道权限、官方评分边界及非 Claude 模型兼容性前置条件；未改动执行代码。

## [2026-09-09] inventory | Benchmark 接入清单

- 新增 [[benchmark-onboarding-checklist]]，区分插件实现、作者 Agent、第三方移植和目标可运行状态。
- 根据用户确认的 Fusion 无内置搜索约束，将联网赛道标为需外部执行流程；未改动历史运行协议。

## [2026-09-09] design | Fusion Router 横向评测提案

- 新增 [[fusion-router-evaluation-design]]，明确当前实现缺口、多目标比较、成本与延迟口径及分阶段验收。
- 提案与已实现事实分别标记，未变更运行代码、现有 target 策略或执行付费调用。

## [2026-08-24] change | 不可变系统失败恢复

- 新增来源 run 驱动的 recovery 选择，只重跑 `pending` 与 `system_failed` case。
- 来源、选择策略、题数和选择集哈希进入派生 manifest；来源 ledger 不被修改。
- Recovery 子集保持 internal-only，并生成按 case 合并的完整固定分母 evidence。

## [2026-08-24] change | 题目级并发与可恢复请求预算

- Runner 支持冻结的题目级并发度，默认串行且不改变单题 VOHU 编排。
- SQLite 在真实调用前原子预留 attempt 和全局请求额度，保证并发上限与断点续跑语义。
- 并发度进入不可变 run manifest 和标识，响应解析与评分保持串行。

## [2026-08-15] govern | Targets 本地化

- `targets/` 调整为被 Git 忽略的本地运行输入，不再提交真实 allowlist 或 composition。
- 默认离线验证不依赖该目录；执行评测时仍要求本地 target 完整并保持 fail closed。

## [2026-08-15] restructure | 聚焦评测实现并完成脱敏

- 删除历史实验流水、外部模型宣传研究、账号操作、部署状态与跨仓库运维信息。
- 将长期知识重组为运行架构、Benchmark 接入、评分证据和测试门禁四个实现页面。
- 具体数据源、版本与校验值回归代码 manifest 管理，知识库不再复制运行态值。
- 重写任务与经验页面，新增禁止记录敏感和运行流水信息的长期边界。

## 相关页面
