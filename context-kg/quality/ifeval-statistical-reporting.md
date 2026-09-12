---
title: IFEval 统计与导出口径
tags: [quality, ifeval, statistics]
links: [scoring-evidence]
updated: 2026-09-09
sources: 0
---

# IFEval 统计与导出口径

汇总主指标为 prompt strict：一题内全部指令通过才记为正确。作业完成且所有题评分成功时才输出正式准确率；运行中只显示固定计划分母的临时值，并输出评分覆盖率。失败与未评分题不得通过缩小分母提升成绩。

结果 API 与 CSV/JSON 使用相同投影，提供 prompt strict、prompt loose、instruction strict、instruction loose。instruction 指标按所有指令微平均，不对各题通过率做宏平均。四项完整指标要求每题具有完整评分证据且 case ID 不重复；旧数据缺失时返回空值。

完成的真实作业提供 prompt 准确率的 Wilson 95% 区间。它只是基于二项近似的描述性区间，不衡量多次生成的稳定性，也不是两个变体差异的显著性检验。instruction 同题内有关联，不直接套用独立指令二项区间。

逐题导出保留 case ID、请求标识、ACP serialized effort、provider effective effort、整题耗时和 agent duration。未确认的 effective effort 保持为空。整题耗时包括执行、评分与清理，不等于模型 API 响应耗时；agent duration 也包含运行环境准备，不能当作 TTFT。

实际结算成本、价格上界估算与预算预留分开。缺少账单时实际成本为空；置信区间和完整评分不能使未结算成本变成真实成本。

## 相关页面

- [[fusion-roadshow-protocol]]

- [[scoring-evidence]]
