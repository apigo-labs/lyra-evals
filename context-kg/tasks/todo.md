---
title: VOHU Evals 任务计划与 Review
tags: [tasks, vohu]
links: []
updated: 2026-09-09
sources: 0
---

# VOHU Evals 任务计划与 Review

## 结果中心与真实执行（2026-09-09）

- [x] 作业和逐题结果共用 API 投影；CSV/JSON 筛选导出与结果图
- [x] IFEval / GPQA / LiveCodeBench 的真实 ACP 执行、隔离出口与官方评分
- [x] 请求前价格上界预留；用量、请求 ID、未结算/估算费用分列
- [ ] τ² ACP 交互与用户模拟器全链；SWE 实例工作区与实际修复链
- [ ] 平台账号授权下的最终账单自动对账；目前不以估算冒充实扣

## 五赛道接入（2026-09-09）

- [x] 选择供应商目录内代表模型并保存本地私有连接
- [x] 五项数据全部准备完成、哈希冻结和 Agent 输入白名单验收
- [x] GPQA 作者公开 Diamond 导入与严格答案解析
- [x] LCB 官方评分器 Docker；正确/错误程序验收
- [x] τ² 官方 mock Gym 断网工具/结束验收；SWE 官方空 patch 执行与报告解析验收
- [x] 新计划绑定数据版本与抽样 case IDs
- [x] 离线测试、前端构建与官方数据/评分接口验收；未发起模型调用
- [ ] 五赛道实际模型/ACP/计费/隔离端到端验收；不能用接口或题库存在代替

## Effort 矩阵实现（2026-09-09）

- [x] 离线 Planner、去重/规模上限、冻结计划与能力状态 API
- [x] Console 多 effort、Harness、预算策略、预览、历史计划和导出
- [x] 独立原子预算预留与幂等结算组件
- [x] Codex 配置生成、ACP effort 协商、冻结镜像配方与断网握手
- [x] 验证：125 个离线测试、前端类型/构建、九个合成容器任务、取消清理、双 ACP 握手
- [ ] 能力预检证据与模型原生参数映射接入实际出口
- [ ] Gateway 单出口、真实计费与预算组件贯通
- [ ] 五赛道真实任务调度、官方评分完整接入与结果比较曲线

### Review

当前交付为可操作的离线计划层与独立执行基础组件；真实模式持续关闭，未调用模型。不能把镜像握手、候选 effort 或单题费用上限解释为正式评测就绪。默认测试不消费模型预算。

## 提交并推送当前改动（2026-09-07）

- [x] 检查当前分支、远端和工作区状态
- [x] 审阅全部待提交差异，确认无敏感或越界内容
- [x] 运行 `make verify`
- [x] 提交全部改动并推送到 `origin/main`
- [x] 核验远端同步状态并补充 Review

### Review

- 本次功能差异仅更新 Judge 测试使用的本地 Gateway 主机名，不涉及生产配置、真实 target、凭据或运行产物。
- `make verify` 已通过：112 个测试、Ruff、Context-KG 校验、敏感信息扫描和 diff 检查均成功。
- 全部工作区改动统一提交到 `main` 并推送至 `origin/main`，推送后核对本地与上游引用一致且工作区干净。

## Publication 系统失败恢复（2026-08-24）

- [x] 聚合诊断 system_failed、retry_count 与原子请求预留
- [x] 证明来源 run 已恰好耗尽冻结请求额度且不能原地续跑
- [x] 为 pending/system_failed 选择集和 combined evidence 增加红测
- [x] 实现 `--retry-from` 不可变派生 recovery run
- [x] 冻结来源、选择策略、case 数量和选择集哈希
- [ ] 完成全仓验证、提交推送并串行重跑 recovery case
- [ ] 生成来源加 recovery 的完整固定分母汇总

### Review

- 来源 ledger 保持只读；Recovery 只包含来源中的 `pending` 与 `system_failed`，已成功 case 不重复请求。
- Recovery 子集 evidence 永远不能单独发布；combined evidence 按 case ID 替换来源失败结果，并合并实际请求与成本。
- 真实恢复运行使用独立 run ID 和足以覆盖每题三次 attempt 的请求额度，避免再次因旧 run 预算耗尽而停机。

## 题目级并发与可恢复请求预算（2026-08-24）

- [x] 冻结题目级并发边界，不改变单题 VOHU 内部编排
- [x] 为并发峰值、全局请求上限和崩溃续跑增加确定性测试
- [x] 在 SQLite 中事务化预留 attempt 和请求额度
- [x] 增加 `--max-concurrency`，并纳入不可变 run 标识和 manifest
- [x] 运行完整测试、格式、Context-KG 和敏感信息校验

### Review

- 默认并发度仍为 1；调用方可显式设置 1 到 32。Runner 仅同时处理不同题目，单题的 Router、Expert、
  Finalizer 关系继续完全由 VOHU Engine 决定。
- 请求在访问 Gateway 前即持久化预留；多个 worker 通过 SQLite 事务共享同一个 `max_requests` 上限，
  中断后恢复时不会重复使用已预留 attempt。
- 解析和评分在进程内串行执行；网络调用与 Platform audit 可并发，以降低墙钟时间且不改变赛道评分语义。
- 并发度属于冻结运行参数；修改它会生成不同 run ID，既有 ledger 不会被新参数覆盖。

## IFEval Auto 实测（2026-08-23）

- [x] 核对 OrbStack 运行镜像与 Platform 实时 Auto 投影
- [x] 为动态 Auto 组合增加“实际模型为冻结模型池子集”的评测契约
- [x] 冻结本地 Auto v2 target 与 IFEval smoke 预算
- [x] 运行无题目真实 preflight，核验 attempt、usage 与结算可用性
- [x] 运行 IFEval 20 题 smoke，固定分母保留系统失败
- [x] 生成 evidence 并对比历史 IFEval 质量与单位成本

### 边界

- 被测入口固定为 APIGO Gateway 的 `apigo/vohu-auto` Chat Completions，不直连内部模型。
- Auto 每题可只调用 Router，也可调用 Router、Expert、Finalizer；只允许使用冻结模型池中的非空子集，不能沿用静态模式“每题必须出现全部模型”的判定。
- 本次实时 composition、运行账本、逐题回答、费用和执行标识仅保存在忽略目录，不进入 Git 或 Context-KG 长期知识。
- 预算上限是熔断值，不是费用目标；最终以 Platform 审计成本和固定分母得分为准。

### Review

- Auto profile 使用动态模型池子集契约，静态模式继续保持精确组合校验。
- 预检会拒绝空答案、失败 attempt 和零 usage；默认仍要求 Platform 已结算成本。
- 本地开发环境可显式保留“usage 已核对但成本未结算”的事实，用于内部 smoke；该状态不能发布为真实零成本，也不放宽 publication 默认门禁。
- 运行账本、逐题回答、实时组合和成本证据继续只保存在 Git 忽略目录。

## Targets 本地化（2026-08-15）

- [x] 核对 `targets/` 的 Git 跟踪状态与代码、测试依赖
- [x] 将 `targets/` 从 Git 索引移除并加入本地忽略规则
- [x] 让不含 `targets/` 的全新 checkout 通过离线验证
- [x] 更新实现文档与经验边界
- [x] 确认本机 target 文件未删除，运行完整验证
- [x] 提交并推送变更

### 边界

- `targets/` 保存运行时 composition 与 allowlist，只作为本地输入，不进入 Git。
- 默认离线验证不得依赖真实 target 内容；需要执行评测的命令在本地文件缺失时保持 fail closed。
- 不创建包含真实模型组合的跟踪模板或测试快照。

### Review

- 21 个 target 文件已从 Git 跟踪中移除，本机文件数量与内容校验保持不变。
- CLI 基础校验允许本地目录尚未物化；fixture 与真实运行仍需显式提供完整 target 输入。
- 策略测试使用临时合成输入，不读取本机真实配置，也不向仓库写入真实快照。
- 不含 `targets/` 的干净 worktree 已通过 101 个测试、Context-KG 校验、secret scan 与 diff 检查。

## Context-KG 脱敏与实现聚焦（2026-08-15）

- [x] 审计全部页面、链接和潜在敏感或越界内容
- [x] 删除运行流水、外部宣传研究与跨仓库运维信息
- [x] 按当前代码重写架构、Benchmark 接入、评分证据和测试门禁
- [x] 同步结构约定、索引、变更日志与经验记录
- [x] 运行脱敏扫描、Context-KG lint 和仓库完整验证
- [x] 提交并推送清理结果

### 边界

- 知识库只记录 `vohu-evals` 的稳定实现、接口边界、质量门禁和当前任务。
- 不记录账号、地址、凭据、执行标识、运行标识、费用、部署状态、故障现场或个人身份。
- 不保存外部模型宣传分数、历史实验流水、逐题结果或与其他仓库实现相关的运维细节。
- 数据集与评测器的具体来源、版本和校验值以代码中的 Benchmark manifest 为权威，知识库只描述冻结机制。

### Review

- 页面由 12 个、1870 行收敛为 9 个、352 行，删除 6 个实验、运行和外部研究页面，新增 2 个实现页面。
- 当前页面只覆盖运行架构、Benchmark 接入、评分证据、测试门禁、结构元数据和任务经验。
- 敏感值与运行流水特征扫描无命中；两套 Context-KG 校验、仓库测试和 secret scan 均通过。

## 相关页面

## Console 接入修复（2026-09-09）

- [x] τ² 官方 Gym / MCP 工具桥、隔离模拟器与预算合并。
- [x] SWE 兼容 evaluator、官方工作区/编译产物、工具环境和断网评分；所选实例可在 Console 准备。
- [x] 结果成本/耗时/准确率导出、精确账单同步入口与未知费用处理。
- [x] 内测模型报价缺失时允许冻结供应商确认的费率上限，不推断模型不可用。
- [ ] 配置实际平台账单权限并完成结算验收。
- [ ] 确认 Fusion 内测费率上限并完成真实模型/effort 对照矩阵。
