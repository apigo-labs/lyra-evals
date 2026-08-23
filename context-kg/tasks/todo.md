---
title: VOHU Evals 任务计划与 Review
tags: [tasks, vohu]
links: []
updated: 2026-08-23
sources: 0
---

# VOHU Evals 任务计划与 Review

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
