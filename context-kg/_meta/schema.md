---
title: VOHU Evals Context-KG 结构约定
tags: [meta, schema]
links: []
updated: 2026-08-15
sources: 0
---

# VOHU Evals Context-KG 结构约定

## 范围

- `technical/adr/`：评测器架构与稳定技术决策。
- `quality/`：Benchmark 接入、评分证据与测试门禁。
- `tasks/`：当前计划、Review 和可复用经验。
- `_meta/`：结构、索引和变更日志。

## 内容边界

- 只记录 `vohu-evals` 当前代码可以证明的稳定事实，不记录聊天过程或运行流水。
- 不记录账号、地址、凭据、个人身份、执行或运行标识、费用、部署状态和逐题内容。
- 不保存外部模型宣传材料或其他仓库的实现与运维信息。
- 易变化的版本、摘要和数据源位置以代码配置为权威，知识库描述机制而不复制具体值。
- 每页必须维护 frontmatter、全局唯一 basename、索引和一致的双向链接。

## 相关页面
