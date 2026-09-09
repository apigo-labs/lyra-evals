---
title: Lyra 本地 Console 与并行 ACP 评测完整设计
tags: [architecture, console, execution, design]
links: [index, benchmark-onboarding-checklist, fusion-router-evaluation-design]
updated: 2026-09-09
sources: 8
---

# Lyra 本地 Console 与并行 ACP 评测完整设计

## 目标、范围与交付边界

用户在本地 Console 配置 APIGO 模型入口、凭据、模型名称与协议；选取一个或多个评测集及目标，创建可重复实验；系统将独立题目委托给容器内冻结版本的 Codex ACP 或 Claude Agent ACP，允许同一评测集多题并行，也允许多个评测集并行。结果用于比较任务质量、实际用户成本、端到端时间和可靠性。

当前选定 IFEval、GPQA、LiveCodeBench、τ²-bench、SWE-bench。GPT 使用 Codex，Claude 使用 Claude Agent，其他目标按已验证能力选择；各赛道具有固定的工具与上下文 profile；官方数据、环境与评分器保持独立。Fusion 无内置搜索，本轮不依赖 WebSearch/WebFetch。

本文中的完整架构是目标设计；以下交付矩阵区分本轮代码与后续适配，不能以 UI 存在推断所有 benchmark 已能真实运行。

| 能力 | 已实现 | 当前限制 |
|---|---|---|
| 本地 Console | 总览、运行、模型连接、评测集、主题、结果与导出 | 本机单进程控制器 |
| 结果中心 | 作业汇总、逐题明细、模型/评测集筛选、CSV/JSON、成本/准确率/耗时图 | 不跨评测集或 Harness 合并排名 |
| 指标 | 最终/暂计准确率、平均/P50/P95 单题耗时、总费用、每正确题成本、估算与预留 | 未结算费用为空；失败与未完成保留固定分母 |
| 真实 ACP | 五赛道调度、冻结镜像与题集、官方评分；τ² Gym/MCP、SWE 工作区与工具环境 | 真实模型/effort 矩阵未全量验收；账单需要平台权限，内测模型需要已确认价格上限 |
| Gateway 网络 | 每题独立 internal 网络，Agent 仅连接专用转发容器；密钥仅转发器可见 | 转发器只允许冻结模型与 Messages/Responses 路径；拒绝远程上下文和联网工具 |
| 预算 | 所有单题上限之和不得超过授权；请求前按整微美元预留，不退款重用 | 公开价格含缓存/长上下文上界，非最终账单；平台账单需账号权限 |
| Effort | ACP 配置确认且出站请求核对；不匹配拒绝 | effective effort 保持未知，不把不同模型 effort 当作相同算力 |
| 取消/失败 | 清理 Agent、转发器、独立网络；保留请求 ID 与费用证据 | 原始回答及诊断仅保存在本地忽略目录 |

结果接口 `GET /api/results` 默认只包含真实运行；支持 `run_id`、`benchmark`、`model`、`include_synthetic`、`level=jobs|episodes`、`format=json|csv`。界面与导出使用同一投影，CSV 防止公式注入；不导出密钥、题干或原始回答。运行变体、数据和执行哈希保留在作业导出中。

正式计划生成仍不付费。点击预算授权执行后，控制器检查真实可执行赛道、公开价格上界与镜像，冻结执行 manifest 再创建任务。所有请求的未决预留在异常后保留，避免超时自动重试造成漏算。

## 产品信息架构

1. 总览：运行数、模型数、赛道数、已结算费用；当前执行曲线、环境门禁、近期记录。空数据明确展示空态。
2. 模型连接：配置名称、APIGO 基础 endpoint、模型 ID、协议和凭据状态。完整 key 不回显，不进入 URL、localStorage、Zustand、日志或证据。
3. 新建评测：选赛道/模型、题数、trial、并发及预算；自检与真实模式显式分开。提交失败显示具体门禁，保留表单输入。
4. 运行详情：作业状态、完成题数、正确数、费用、执行曲线、事件时间线、取消与 JSON 证据导出。导出包含冻结 manifest。
5. 评测集：实际代码接入状态、工具权限、数据规模与阻塞项；未知题数不填虚构数字。
6. 主题页：真实组件组成的主题样本，色板、字体层级、按钮、状态、交互原则；样例数字明确标为排版样本。

不默认选出赢家。自检通过不是任务准确率；部分完成不是全题集成绩；待结算不能显示为零；同一指标必须附分母和单位。

## 前端架构与主题

采用用户指定的 React + Vite + TypeScript + Tailwind CSS v4 + shadcn 风格源码组件/Radix 原语 + Zod + Zustand + TanStack Query。Radix Dialog 处理焦点、关闭和键盘行为；Button 使用 CVA 变体。`components.json` 保留 shadcn 配置，后续共享组件继续沿用相同约定。

- TanStack Query：服务器数据、mutation 后失效与后台同步。所有 HTTP 返回经 Zod 校验。
- Zustand：仅当前视图与选中运行，不复制服务器数据和敏感字段。
- React 表单：API Key 仅在提交时短暂存在，表单卸载/成功保存后清除；不使用持久化 mutation 存储。
- SSE 只提示数据版本变化；客户端重连后从 API 拉完整快照，事件断流时轮询兜底。后续大型运行使用 cursor 分页。
- 生产构建由本地 API 同源提供；开发 Vite 固定回环地址并代理 `/api`。不要求云端站点、外部字体或 CDN。

### Paper / Ink 主题

| Token | 色值 / 规则 | 使用 |
|---|---|---|
| background / card | `#ffffff` | 主内容、卡片、弹窗 |
| canvas | `#fafafa` | 工作区底色 |
| primary / foreground | `#171717` | 主要操作、标题、图表主线 |
| muted foreground | `#737373` | 正文辅助信息，不承载唯一状态 |
| border | `#e9e9e9` | 轻分隔与组件边界 |
| typography | 系统字体；中文 PingFang 回退 | 不下载或分发 Apple 字体 |
| numeric | tabular-nums | 费用、题数、耗时避免跳动 |
| radius | 控件约 10px，内容卡约 14px | 统一组件层级 |
| motion | 轻量过渡；支持 reduced-motion | 运行图禁用重复绘制动画 |

从 [Apple HIG Layout](https://developer.apple.com/design/human-interface-guidelines/layout)、[Typography](https://developer.apple.com/design/human-interface-guidelines/typography)、[Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility) 提炼的是清晰层级、稳定布局、可读文字和键盘可达性；不把 Apple 的平台效果照搬成大面积玻璃或装饰。

图表让数据本身最醒目，轴线和说明弱化；状态不只依赖颜色，提供文本、图形与可导出原数据。这与 [Apple HIG Charts](https://developer.apple.com/design/human-interface-guidelines/charts) 的层级和可访问性原则一致。

### 图表方案与 lieflat-charts 调研

[lieflat-charts](https://github.com/larashero3-dotcom/lieflat-charts) 是生成交互 HTML 图表/报告的 Agent Skill，而非可直接安装的 React 实时图表库。其 Mono 风格与本需求一致，可参考线条、留白和单位表达。

本轮不拷贝其模板、不安装其 Skill、不将生成 HTML 放入主 Console；实时图采用 Recharts 与本地主题，数据来自运行事件。后续若要离线报告模板，先核验对应版本许可证和第三方声明，采用隔离导出流程，不能让报告 JS 访问凭据。

图表规划：

| 视图 | 数据来源 | 边界 |
|---|---|---|
| 累计完成曲线（已实现） | Episode 完成事件 | 横轴为完成序号，不伪称等间隔时间吞吐量 |
| 作业进度（已实现） | completed / total | 失败终态纳入进度，正确数另报 |
| 费用时间线（待真实账单） | 去重后的结算事件 | 未结算区间保留缺失，不连成零费用 |
| 耗时分布（待正式结果） | 完整 Episode 时长 | 失败、超时单列，不能仅看成功请求 |
| 成本—质量散点（待配对比较） | 同题同协议汇总 | 置信区间及可比性通过后才展示排行榜 |

## 执行拓扑与 ACP 所处位置

```text
Browser Console
  └─ Local API / Store / Planner / Scheduler（受信任，持有 Docker 控制权）
       ├─ Job queue: benchmark × variant × trial
       ├─ Episode: fresh Docker sandbox
       │    └─ Codex ACP 或 Claude Agent ACP adapter / runtime
       │          ├─ 允许的本地工具
       │          └─ 固定 Gateway 出口 → APIGO → Fusion / 单模型
       ├─ 独立 scorer / 官方验证环境
       └─ Audit & billing reconciliation → evidence → report
```

ACP 不创建 Docker；Scheduler 先创建沙箱，随后 ACP client 用 stdio 控制沙箱内 Agent。ACP 负责 initialize、session/new、prompt、update 和取消；容器创建、资源上限和清理属于 Scheduler。

[claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp) 使用 Claude Agent SDK，不是任意版本 Claude Code CLI 的别名。分别冻结 adapter、SDK、运行时依赖和 image digest；目前配方固定 adapter 0.63.0、SDK 0.3.220，并提交 npm lock。

Claude ACP 需要其支持的模型协议。现有 Console 可记录三种协议，但不会将 Chat Completions 自动假装成 Anthropic Messages。APIGO 提供的每个目标必须先通过对应兼容性测试。非 Claude/Fusion 的官方支持限制及适配边界见 [Claude Code Gateway 文档](https://code.claude.com/docs/en/llm-gateway)。

## 并行语义与调度

- Experiment：一次用户提交，冻结 benchmark IDs、target snapshots、trial、题目选择和预算。
- Job：一个 `benchmark × variant × trial`，不要求把整个题集塞进一个持续共享状态的容器。
- Episode：一题的一次执行，默认独立容器、独立上下文。该设计避免同题集内答案、记忆和文件互相污染。
- 同时运行 Job 数由 `max_jobs` 限制；每 Job 题目数由 `per_job` 限制；每实验容器数由 `max_episodes` 限制；控制器另有跨实验全局容器上限（当前 8）。
- 所有模型共用相同任务 profile；任务顺序、样本 ID 集合和 seed 应冻结。真实运行分块交错目标顺序，避免不同时段的供应商状态造成系统偏差。
- 第一版单进程异步调度，SQLite 在同一事件循环写入。未来容量需要时再引入独立 worker/租约；不先建设 Kubernetes 或分布式队列。
- 取消先停止新任务派发、终止子任务并移除任务容器，最后保存终态。进程重启将未完成运行标为 interrupted，不自动再次发起可能已收费的调用。强杀后需按受管名称对残留容器对账清理。

## 状态与数据契约

目标数据形态：`Experiment → Job → Episode → Step → Request → Execution/BillingEvent`。不以单次 HTTP 请求代表完整 Agent 任务。

| 实体 | 关键字段 |
|---|---|
| TargetSnapshot | id、name、endpoint、model、protocol、secret reference、版本 |
| ExperimentManifest | schema version、题集/hash、目标快照、Harness/profile/hash、镜像 digest、预算、并发、seed |
| Episode | case ID、trial、容器标识、ACP session、执行状态、开始/结束、输出工件引用 |
| Step / Request | 角色、工具、request ID、实际模型、usage、重试关系、起止时间 |
| Score | scorer 版本、值与单位、细分指标、判定证据、Judge 调用关联 |
| BillingEvent | 唯一 ID、金额精度、状态、父子包含关系、价格版本 |
| Event | 单调 seq、时间、实体 ID、类型、非敏感摘要 |

当前 Console 将运行 manifest、作业及事件作为 JSON 快照保存到 SQLite，适用于有严格规模限制的本地自检。正式大规模数据需拆分上述表，并让事件 append-only。历史 run 不随模型配置修改；报告不持有 key。

目标执行、评分、结算分别拥有状态机。执行成功但评分失败只重评分；结算晚到只补账单；不重复执行模型以修补报告。

## 安全与网络

- 本地服务绑定 `127.0.0.1:8768`；不支持公网多人使用。Host/Origin 校验、拒绝跨站请求、自定义 JSON 写请求标识防止跨站网页驱动本地 Docker。
- API Key 保存于被 Git 忽略的 `.local/console/secrets/`，目录 0700、文件 0600；这是本机文件权限保护，不是加密存储。后续可接系统 Keychain；当前不宣称加密。
- 运行容器非 root、cap-drop ALL、no-new-privileges、只读根目录、限定 tmpfs、CPU/内存/PID 限制、不暴露 Docker socket。
- 自检 network=none 已提供；正式网络需创建只有固定出口的任务网络，由受信任转发器注入受限凭据，仅转发模型 API，不允许任意 CONNECT/URL/重定向。仅设置 BASE_URL 或 HTTP_PROXY 不是隔离。
- 正式镜像和题目环境在准备阶段获取依赖，执行期间禁止公网安装。DNS、IPv6、宿主、其他容器和绕过代理路径必须逐项验收。[Docker 网络规则](https://docs.docker.com/engine/network/packet-filtering-firewalls/)需按具体部署后端实现。
- ACP 的宿主文件/终端回调默认拒绝；后续若提供，必须映射到当前 Episode 沙箱。τ² 工具桥只暴露允许的业务 API，不暴露数据库和隐藏答案。
- Judge/用户模拟器/审计在控制器侧使用独立凭据；它们的权限不传给被测容器。完整请求和原始答案只保存在本地受控工件目录。

## 预算、质量与成本口径

正式执行必须明确确认预算。预算应包含被测模型、Fusion 内部实际收费、外层 Agent 重试、工具、Judge 与用户模拟器等评测支出；服务任务成本与评测运营开销分栏。

并发调用前预留最坏允许费用，结算后释放差额；未知费用保留预留且阻止继续派发。Gateway 专用 key 硬预算是最后防线。现有 Console 的预算表单本身不是费用强制保障，因此正式执行在对账与预算能力验收前拒绝。

任务成功率以冻结任务数为分母；错误、失败、超时不删除。每正确任务成本以所有任务成本为分子。IFEval 分开报告整题 strict 与指令 strict；代码 pass@1；τ²/SWE 以官方结果状态为准。跨模型做同题配对比较及区间，不能把合成任务自检通过率当模型质量。

## API 与运行方式

| API | 功能 |
|---|---|
| GET /api/health | Docker、镜像和正式执行门禁 |
| GET /api/benchmarks | 固定五赛道的实现状态与阻塞原因 |
| GET / POST / DELETE /api/targets | 脱敏模型配置管理 |
| GET / POST /api/runs | 读取/冻结并提交运行 |
| POST /api/runs/{id}/cancel | 取消并等待清理 |
| GET /api/events | SSE revision 通知 |

本地开发/启动命令以 README 与 Makefile 为权威。前端在 `console/`，控制器在 `src/vohu_evals/console/`，镜像在 `deploy/console/`。默认 legacy CLI 继续保留，中文协作与原始数据不入 Git 的规则不变。

## 分阶段验收

1. Console 闭环：模型可保存/删除，凭据不回显；非法配置与跨站请求被拒绝；所有页面有空态/错误态；前端构建与 API 契约检查通过。
2. 自检执行：真实 Docker 上运行至少两个评测集，观察全局并发上限、取消清理、事件与重启终态；结果明确标识 synthetic。
3. Claude ACP：冻结镜像可 initialize；在显式预算允许后验证 APIGO 目标身份、无工具及 tools 多轮协议。仅握手不能声明真实模型兼容。
4. Gateway 出口与计费：旁路网络被拒绝、凭据受限、预算预留和结算去重可靠，才启用真实任务。
5. 逐赛道接入：IFEval → GPQA/LiveCodeBench → τ²-bench → SWE-bench；每项先验证官方 scorer parity，再开放正式按钮。

## 相关页面

- [[index]]
- [[benchmark-onboarding-checklist]]
- [[fusion-router-evaluation-design]]
