# 路演展示页设计依据（2026-09）

面向对象：负责实现静态 HTML 展示页的 agent。
数据来源：`.local/inspect-report-main/report.csv` / `report.json` / `samples.csv`（2026-09-12 ~ 09-14 跑批）。
本文档区分两类内容：**【引用】**= 外部来源的做法或结论，附链接；**【判断】**= 基于本次数据的我方判断。

---

## 0. 结论先行

推荐页面用 4 种图表，外加 1 张披露表。顺序即页面顺序。

| # | 图表 | 解决什么问题 | 为什么选它 |
|---|---|---|---|
| 1 | **成本 vs 准确率散点（对数 x 轴 + Pareto 前沿线）**，三赛道小倍数并列 | 一眼看出 Fusion 在哪个价位段占住了前沿 | 业界通用语言（Artificial Analysis、HAL、RouteLLM 都用这个形态），观众不需要学习成本 |
| 2 | **每个正确答案的成本（cost per correct）横向条形图**，按赛道分组 | Lyra 最站得住的单一数字，把准确率和成本压成一个可比标量 | 有学术先例和命名（SWE-Bench+ 的 effectiveness-aware cost），不是自造指标 |
| 3 | **路由基线阶梯图**（Random / BestSingle / Oracle 三条参考线 + 三个 Lyra 路由点） | 回答"路由到底有没有价值" | RouteLLM / RouterBench 的标准论证结构，评审会预期看到 Oracle 上界 |
| 4 | **延迟 p50/p95 哑铃图（对数 x 轴）** | 主动暴露劣势，控制叙事 | 单独一栏、不和成本混在一张三维图里；比雷达图可读 |
| 5 | **口径披露表**（n、n_scored、error_rate、max_tokens、effort） | 堵住"你这数据怎么来的"的追问 | 不是图，是必须存在的表 |

不推荐：雷达图、三维气泡图（用气泡大小编码延迟）、任何把三个赛道合并成单一"综合分"的做法。理由见 §3、§6。

**一个必须先纠正的口径问题【判断】**：内部说法"auto 比最便宜的固定模型再低一个量级"不成立。实际是 auto 相对**最强单模型**（best single）低一个量级，相对**最便宜的固定模型**（gpt-5.6-luna）只在 IFEval 上便宜 47%、LCB 上便宜 28%、GPQA 上反而贵 21%。页面文案必须写成"相对最强单模型"，否则现场对一下 luna 的价格就穿帮。

---

## 1. 成本 vs 准确率散点

### 出处

- Artificial Analysis 的 Intelligence Index vs Cost per Task 图，是这一形态的事实标准。GPQA Diamond 单赛道视图带 cost-per-task 轴：<https://artificialanalysis.ai/evaluations/gpqa-diamond>；方法论：<https://artificialanalysis.ai/methodology>
- HAL（Holistic Agent Leaderboard，Princeton）：<https://hal.cs.princeton.edu/>，论文 <https://arxiv.org/abs/2510.11977>。首页直接用一句话说明为什么要画前沿：agent 可以贵 100 倍而只好 1%，传统榜单区分不出来。【引用】
- RouteLLM 论文 Figure 1 左图：Chatbot Arena Elo vs 每百万 token 成本（1:1 输入输出假设）的散点。<https://arxiv.org/abs/2406.18665>，博客 <https://www.lmsys.org/blog/2024-07-01-routellm/>
- Epoch AI benchmarking hub：<https://epoch.ai/benchmarks>，方法 <https://epoch.ai/benchmarks/about>

### 通行约定

- **x 轴对数**。业界普遍这么做，原因是价格跨度远大于分数跨度——公开报价从 $0.03 到 $30/1M token 是三个数量级，而分数区间只有十几个百分点。【引用，来源为上述 AA 相关分析】我们的数据同样：每题成本从 $0.000488 到 $0.0876，跨 2.3 个数量级；准确率全部落在 0.81–0.97。线性 x 轴会把所有便宜模型挤成一坨。
- **越靠左上越好**。成本在 x、分数在 y 时，Pareto 前沿是"左上包络"。
- **Pareto 前沿只连非支配点**，画成阶梯线或细连线，不要拟合曲线。`report.json` 的 `baselines.<bench>.pareto_front` 已经算好了固定模型的前沿，直接用。
- 气泡大小：AA 这类图通常不额外编码第三维，或只用它编码无关紧要的东西。【判断】我们不要用气泡大小编码延迟——见 §3。

### 我们的数据适不适合

适合，但有三处要处理：

1. **`pareto_front` 里的成本和 `rows` 里的 `cost_usd_per_sample` 有小幅不一致**（例：IFEval luna，前沿里 0.000933，行里 0.000914）。原因是逐题成本优先按请求时长匹配账单，匹配不上的题回退到运行平均（`cost_fallback_note`，IFEval 共 11 题回退）。画图时**统一用 `rows.cost_usd_per_sample`**，前沿只取模型名单不取数值，避免同一个点在两张图上数字不同。
2. **`cost_usd_estimated_public` 和 `cost_usd_settled` 是两套口径**。固定模型有公开报价估算值，Fusion 没有（字段为空）。页面只用 settled 口径，并在脚注写明"Fusion 无公开报价，全部按实际结算金额计"。
3. **Fusion quality 在 GPQA/LCB 上比最强单模型贵 1.4 倍**（$0.0876 vs $0.0356；$0.0747 vs $0.0305）。它会落在前沿的右下方，也就是被支配区。不要把它藏起来。

### 注意事项

- 前沿是路由地图不是排行榜。【引用】把"不在前沿上的模型就没用"这种话从文案里去掉。
- 快照会过期。页面必须标注跑批日期（2026-09-12 至 09-14）和价格口径日期。【引用：Epoch/AA 都强调快照时效】

---

## 2. 每个正确答案的成本（cost per correct）

### 出处与命名

这个指标有明确先例，不是自造：

- **SWE-Bench+**（<https://arxiv.org/abs/2410.06992>）定义了 "effectiveness-aware cost per instance" = 总成本 ÷ 成功解决的实例数，与"平均每实例成本"并列报告。论文给的例子：SWE-Agent+GPT-4 平均每实例 $0.24，但每个真正修好的 issue 要 $32.50。【引用】
- 多个 agent 榜单用同类列：SWE-bench Multimodal 的 "Avg. $ Cost"、Multi-SWE-bench 的 "Average Cost ($) per issue"、HAL 的 total USD + Pareto 标记。
- 中文命名建议：**「每答对一题的成本」**，英文括注 `cost per correct answer`。不要译成"有效成本"之类容易和财务口径混淆的词。

### 我们的数据

`cost_per_correct_usd` 字段已经有了。核心数字（【判断】，从 report.csv 直接算）：

| 赛道 | Fusion auto | 最强单模型 | 倍数 |
|---|---|---|---|
| IFEval | $0.000558 | gpt-6-astra $0.037124 | 1/66 |
| GPQA Diamond | $0.003285 | gpt-6-astra $0.038814 | 1/12 |
| LiveCodeBench v6 | $0.001995 | claude-opus-5 $0.032168 | 1/16 |

相对**最便宜**的固定模型（luna）则是 1/1.7、1.22 倍、1/1.5——没有量级优势。两组倍数都要放，只放前一组就是挑有利基准。

### 注意事项

- 分母是 `n_correct`，不是 `n_planned`。Fusion 组有 0.8%–12.5% 的样本因上游故障没评上分，这些题既不进分子也不进分母，等于**假设损耗样本的成本和正确率与成功样本同分布**。这条假设必须写进脚注。
- 这个指标在准确率接近 1 时对准确率不敏感，在准确率低时会放大差距。我们三个赛道准确率都在 0.81 以上，指标行为稳定，可以用。
- 不要用它替代准确率。它是第二张图，不是第一张。

---

## 3. 效率/延迟怎么和成本、准确率一起看

### 出处

- Artificial Analysis 性能方法论：<https://artificialanalysis.ai/methodology/performance-benchmarking>。关键约定【引用】：(a) 头条数字用**中位数 P50**，取过去 72 小时窗口；(b) 拆成 TTFT（首 token 时间，推理模型算的是第一个推理 token）、thinking time、output tokens/s、以及派生的"输出 500 token 的端到端秒数"；(c) 对推理模型另报 "time to first answer token"。
- 雷达图批评：<https://blog.scottlogic.com/2011/09/23/a-critique-of-radar-charts.html>、<https://peltiertech.com/radar-plots/>。要点【引用】：多序列必然遮挡（实践上限 3–4 个多边形）；轴的排列顺序任意但显著改变多边形形状；比较的是面积不是数值，0.85 和 0.87 看不出来；不同量纲的轴用线连起来暗示了不存在的关系。
- 小倍数（small multiples）作为替代：同一批批评文章推荐的方向是固定坐标范围、固定类别顺序的分面小图，或热力图/点阵图（模型为行、指标为列）。【引用】

### 三维降维怎么选

【判断】我们有准确率、成本、延迟三维，可选方案：

1. **散点 + 气泡大小编码延迟**——不推荐。气泡面积的感知精度很差，而我们的延迟跨度是 4.34s 到 185.63s（p50），气泡会大到互相覆盖。
2. **雷达图**——不推荐。8 个变体 × 3 指标，遮挡严重，且成本/延迟/准确率量纲完全不同。
3. **小倍数：三个赛道 × 一张成本-准确率散点**，延迟单独一张图 —— 推荐。
4. **热力图（行=变体，列=指标，按赛道分块）** —— 可作为页面底部的"全量数据"替代表格。

### p50 还是 p95

【判断】**两个都放，画哑铃图**（一条横线连接 p50 和 p95，两端各一个点）。理由：

- AA 的行业惯例是 P50 做头条【引用】，但只报 p50 会完全掩盖我们的问题。Fusion quality 在 GPQA 上 p50 185.63s、p95 832.97s；budget 在 LCB 上 p50 38.52s、p95 482.70s。p95/p50 比值达到 4.5–12.5 倍，而固定模型里最极端的 gpt-6-astra 在 LCB 上是 301.98/15.84 ≈ 19 倍——固定模型的长尾也不干净，哑铃图能同时说明这点，对我们反而有利。
- 哑铃图 x 轴同样用对数。

### tokens/s 还是端到端时延

【判断】用**端到端时延**（`latency_p50_s` / `latency_p95_s`，即每题完整耗时）。原因：Fusion 是路由产品，一次请求可能包含路由决策 + 一次或多次上游调用，tokens/s 不是一个有定义的量。同时页面要给出 `output_tokens_per_sample` 作为解释性数据——Fusion quality 在 GPQA 上每题输出 19991 token，是 astra（663.6）的 30 倍，这才是慢的直接原因，比单纯说"慢"更可信。

不要用 `wall_time_s`（整轮墙钟时间）做跨变体比较——它受并发度和限流影响，不是产品属性。

---

## 4. 置信区间与小样本

### 出处

- Evan Miller / Anthropic, *Adding Error Bars to Evals*：<https://arxiv.org/abs/2411.00640>，博客 <https://www.anthropic.com/research/statistical-approach-to-model-evals>。五条建议中与我们相关的三条【引用】：报告标准误并给 95% CI；**优先做配对差分**（比较同题上的两模型差异，比比较两个聚合值的标准误小得多，是"免费"的精度提升）；做功效分析确认最小可检测效应。
- Epoch AI 的做法【引用】：GPQA Diamond 只有约 198 题，单次跑分噪声大，所以对大多数模型重复跑 16 次，主图画 ±1 标准误；他们给出的经验噪声水平是 2–3 个百分点，小于几个点的差异通常没有意义。<https://epoch.ai/benchmarks/about>、<https://epoch.ai/data-insights/self-reported-gpqa>
- GPQA Diamond 自身答案键的错误率也是一项独立不确定性来源：<https://epochai.substack.com/p/gpqa-diamond-whats-left>

### 我们的处境

【判断】n=60（GPQA）和 n=120（IFEval / LCB），单跑一次，没有重复采样。Wilson 区间宽度：

- GPQA，acc≈0.90，n=60 → 大约 [0.80, 0.95]，半宽约 ±7.5pp。
- IFEval / LCB，acc≈0.92，n=120 → 大约 [0.86, 0.96]，半宽约 ±5pp。

**结论：GPQA 上所有八个变体的 Wilson 区间彼此重叠，赛道内不存在统计上可区分的准确率差异。** 页面上凡是提到 GPQA 准确率排名的地方都必须写明这一点。IFEval 上 gpt-6-astra（0.9748，CI 0.9285–0.9914）和 Fusion auto（0.8739，CI 0.8024–0.9221）区间不重叠，这个差距是真的。

### 画法建议

- 散点图上用**垂直误差线**（whisker），不要用误差带/阴影区——8 个点的阴影区会糊成一片。
- 误差线用和点同色但降低不透明度（0.4 左右），线宽 1px，端帽宽度不超过 6px。这样默认视觉重心还是在点上，不显得杂乱。
- **加一条视觉说明**：在 GPQA 面板上画一条水平灰带覆盖所有 CI 的重叠区间，配文案"该区间内的差异不可区分"。这比逐点画误差线更能传达结论，而且是主动诚实。
- **强烈建议补做配对分析**。`samples.csv` 有 `sample_id`，可以对任意两个变体做同题配对比较（McNemar 或配对 bootstrap）。这是 Anthropic 论文明确推荐的做法【引用】，也是唯一能把 n=60 的结论变得有力的手段。至少在页面上给出 Fusion auto vs best single 的配对差分和 CI。

### 损耗率怎么标

【判断】error_rate 不能只放在脚注，因为 LCB 上 Fusion auto 损耗 11.67%，这是准确率数字的最大威胁。做法：

- 散点图上，`error_rate > 5%` 的点加一个视觉标记（描边加粗为虚线，或点右上角一个小三角），hover/图注说明"该点有 X% 样本未评分"。
- **页面上必须给出共同子集（common subset）复核结果**。已经算过（【判断】，从 `samples.csv` 取所有八个变体都成功评分的题）：

| 赛道 | 共同子集 n | Fusion auto 全量 → 共同子集 | 最强单模型 全量 → 共同子集 |
|---|---|---|---|
| IFEval | 118 | 0.8739 → 0.8729 | astra 0.9748 → 0.9746 |
| GPQA Diamond | 54 | 0.8909 → 0.8889 | astra 0.9167 → 0.9259 |
| LiveCodeBench v6 | 100 | 0.9717 → 0.9900 | opus-5 0.9487 → 0.9700 |

这个复核对我们是有利的：LCB 上限定共同子集后 auto 仍然领先（0.99 vs 0.97），说明 11.67% 的损耗不是"把难题丢掉换高分"。把这张表放出来，比闭口不谈更有说服力。GPQA 共同子集只剩 54 题，CI 更宽，要标注。

---

## 5. 路由类产品的专属论证结构

### 出处

- **Martian RouterBench**：<https://withmartian.com/post/introducing-routerbench>。核心框架【引用】：Zero Router（不做路由、等同于候选池平均表现）是**下界**，路由器打不过它就说明路由逻辑没有价值；Oracle Router（全知，每题都选对最优模型）是**上界**，不可达，用来衡量最大可能收益，路由器越接近它越好。
- **RouteLLM**（LMSYS/Berkeley）：<https://arxiv.org/abs/2406.18665>、<https://www.lmsys.org/blog/2024-07-01-routellm/>、代码 <https://github.com/lm-sys/RouteLLM>。图表约定【引用】：x 轴是"调用强模型的比例"，y 轴是性能；**强模型和弱模型的表现各画一条水平虚线**（论文里是红色和灰色点线）作为参照；random router 的曲线作为基线。两个指标：PGR（performance gap recovered，恢复了多少强弱模型之间的差距）、CPT(x%)（达到 x% PGR 所需的最小强模型调用比例）、APGR（PGR 曲线下面积）。
- 表述范式【引用】：RouteLLM 的原话结构是"用 26% 的 GPT-4 调用达到 GPT-4 95% 的表现，比随机基线便宜约 48%"。即 **「达到 X% 的强模型表现，花了 Y% 的成本」**，而不是「省了 Z%」。
- **LLMRouterBench**（<https://arxiv.org/html/2601.07206v1>）和 **RouterEval**（<https://arxiv.org/abs/2503.10657>）：候选池扩大时 Oracle 收益递减，池子从小到中等时增益最大。【引用】
- 反面提醒【引用】：一项独立复现发现四个路由器中有三个表现为常量或近常量选择器，RouteLLM 的分类器在其测试池上 100% 选了便宜模型，从未触发升级阈值。<https://arxiv.org/html/2608.14641>
- OpenRouter Auto Router 作为商业对照：<https://openrouter.ai/openrouter/auto>、路由文档 <https://openrouter.ai/docs/features/model-routing>；Not Diamond：<https://www.notdiamond.ai/>。

### 我们的基线数据

`report.json` 的 `baselines.<bench>` 已经有 `best_single` / `oracle` / `random_uniform` / `pareto_front`，字段齐全，且 `variants` 里已经算了 `cost_vs_best_single`、`gap_to_oracle_pp`、`headroom_captured`、`above_random_mix`。

关键事实【判断】：

| 赛道 | Oracle 准确率 / 成本 | Random 准确率 / 成本 | BestSingle 准确率 / 成本 |
|---|---|---|---|
| IFEval | 0.9833 / $0.001842 | 0.9200 / $0.018132 | 0.9748 / $0.034335 |
| GPQA | 0.9333 / $0.003948 | 0.8893 / $0.020484 | 0.9167 / $0.035579 |
| LCB v6 | 1.0000 / $0.004753 | 0.8986 / $0.018946 | 0.9487 / $0.027945 |

- `headroom_captured` 在 9 个（赛道 × 路由）组合里有 8 个是**负值**，只有 LCB 的 auto 是正的（0.4483）。也就是说按 RouterBench 的框架，大多数配置没有吃到 BestSingle→Oracle 之间的质量空间。
- `above_random_mix` 只有 IFEval auto 和 LCB auto 是"是"。
- **但基线只对齐了准确率维度，没对齐成本**。Fusion auto 在 IFEval 上准确率低于 Random（0.8739 vs 0.9200），成本却只有 Random 的 2.7%。单看 headroom 会得出"没价值"的错误结论。

### 画法建议

【判断】不要照搬 RouteLLM 的 x=强模型调用比例曲线图——我们没有连续阈值扫描，只有三个离散档位（auto/budget/quality），画不出曲线。改用**阶梯参考线图**：

- 一张图一个赛道（三个小倍数），y 轴准确率（**从 0.75 起，不从 0 起——见 §6 的处理方式**），x 轴用离散类别：Random / Fusion auto / Fusion budget / Fusion quality / BestSingle / Oracle，按成本从低到高排。
- Oracle 画成顶部水平虚线并标注 "理论上界（全知选择，不可达）"；Random 画成另一条水平虚线。
- 每根柱子下方直接标该档的每题成本。这样"准确率阶梯 + 成本阶梯"在同一张图里读出来。
- 明确标注 Oracle 的口径：`routable_share`（IFEval 4.17% / GPQA 5.0% / LCB 9.24%，即真正存在"换个模型就能答对"的题的比例）和 `unsolved_share`（IFEval 1.67% / GPQA 6.67% / LCB 0%，即所有候选模型都答错的题）。**routable_share 很小这件事本身要说出来**——它意味着这批题里路由的理论收益空间本来就有限，这既解释了 headroom 为负，也预防了"你的路由没提升准确率"的质问。

### 文案模板

【判断】按 RouteLLM 的表述范式改写，建议固定用这三句：

- 「LiveCodeBench v6 上，Fusion auto 达到 97.2% 准确率（最强单模型 claude-opus-5 为 94.9%），每题成本为其 6.4%。」
- 「GPQA Diamond 上，Fusion quality 准确率 91.5%，与最强单模型 91.7% 的差距在 n=60 的统计噪声之内；但每题成本是其 2.46 倍，延迟 p50 为其 9.9 倍。」
- 「IFEval 上，Fusion auto 每题成本为最强单模型的 1.3%，准确率低 10.1 个百分点（区间不重叠，差距真实）。」

不要写「省了 98.7%」而不说准确率代价。省钱幅度和分数代价必须在同一句话里。

---

## 6. 红线（会被认定为误导的做法）

出处：截断轴 / 挑时间窗 / 双轴不同尺度 / 标签缺失是可视化欺骗的经典清单，AI 模型发布图表被点名的案例见 <https://www.lesswrong.com/posts/EWfGf8qA7ZZifEAxG/ai-benchmarking-has-a-y-axis-problem-1>；报告条件不一致（pass@8 vs single-shot、为自家模型调 prompt 而引用对手已发表数字）属于同类问题。【引用】相关研究还区分了"FACT"（图上数据是否属实）和"MIND"（读者会怎么理解）两个问题——截断轴的图数据通常是对的，问题在感知效果，所以"标签写了啊"不是有效辩护【引用，<https://arxiv.org/html/2508.09716v1>】。

我们的红线清单【判断】：

1. **准确率条形图的 y 轴不从 0 起**。如果确实要放大 0.80–1.00 区间，必须换成**点阵图（dot plot）而不是条形图**——点的位置编码不像条的长度那样依赖零基线，这是可辩护的做法。绝不要画一个从 0.85 起跳的柱状图。
2. **只展示 LiveCodeBench**。auto 在 LCB 上 0.9717 是全场最高，IFEval 上却比最强单模型低 10.1pp。三个赛道必须同时出现在首屏，不能靠 tab 切换把 IFEval 藏到第二屏。
3. **隐藏 n**。每张图、每行数据必须能看到 `n_planned` 和 `n_scored`。GPQA 的 n=60 尤其要显眼。
4. **混用不同 max_tokens 而不标注**。Fusion 在 GPQA/LCB 上用了 32768，固定模型是 16384。这是一个实打实的口径不对称，必须在图例层级标注，不能只放在页脚。
5. **把损耗样本当作不存在**。不能只报 `accuracy`（分母 n_scored）而不报 `error_rate`，也不能只报 `cost_usd_per_sample` 而不提 `cost_usd_per_planned_sample`（两者在 LCB auto 上差 13%：$0.001939 vs $0.001712）。
6. **混用公开报价和实际结算成本**。固定模型的 `cost_usd_estimated_public` 和 `cost_usd_settled` 在 IFEval sol 上差 1.5 倍（$2.22 vs $1.48）。全页只能用一套口径。
7. **不展示延迟**。成本和准确率两张图看完，观众自然会问"那速度呢"。不主动给，就等于让对方来揭。
8. **把 Oracle 当成产品能力**。Oracle 是全知上界，任何"我们接近 Oracle"的表述都要紧跟"Oracle 不可达"。
9. **省略路由实际选择分布**。`samples.csv` 的 `lyra_selected_model` 显示 IFEval auto 的 119 题**全部**路由到了 `deepseek-v4-flash`——即该赛道上 auto 退化成了常量选择器。文献里恰好有同类发现（见 §5 的反面提醒），被问到而答不上来会很难看。其余跑批该字段为空（未记录），这一点也要说明"部分运行未记录路由决策"。
10. **用"一个数量级"这类修辞而不指明基准**。见 §0。

---

## 7. 「必须披露」清单

页面上必须出现（不是脚注里塞一行，而是和数据同屏可见）：

| # | 披露项 | 具体内容 |
|---|---|---|
| D1 | 样本量 | IFEval n=120、GPQA Diamond n=60、LiveCodeBench v6 n=120，单次运行，无重复采样 |
| D2 | 统计可区分性 | GPQA 赛道内所有变体 Wilson 95% 区间重叠，准确率排名不具统计意义 |
| D3 | 样本损耗 | Fusion 组 `error_rate` 0.83%–12.5%（最高为 LCB auto 11.67%，错误类型 upstream 13 + scorer 1）；固定模型 0%–8.33%。准确率分母为 `n_scored` |
| D4 | 损耗偏差复核 | 给出共同子集准确率对照表（§4），说明损耗未系统性抬高 Fusion 分数 |
| D5 | max_tokens 不对称 | GPQA/LCB 上 Fusion = 32768，固定模型 = 16384；IFEval 双方均为 8192 |
| D6 | effort 口径 | 固定模型全部 `reasoning_effort=high`；Fusion 无该参数，由路由自行决定 |
| D7 | 成本口径 | 全部为实际结算金额（`cost_usd_settled`）。逐题成本优先按请求时长匹配账单，未匹配的回退到运行均值（IFEval 11 题回退，见 `cost_fallback_note`）。Fusion 无公开报价对照 |
| D8 | 延迟劣势 | Fusion quality 在 GPQA 上 p50 185.6s / p95 833.0s，是最强单模型（astra 18.7s / 61.0s）的约 10 倍 / 13.6 倍。原因是每题输出 token 多 30 倍 |
| D9 | 赛道饱和 | IFEval 全部变体准确率 0.867–0.975，天花板效应明显；GPQA Diamond 的答案键本身有非零错误率（Epoch 分析，见 §4 链接），0.92 以上的差异需谨慎解读 |
| D10 | 路由空间有限 | Oracle 的 `routable_share` 仅 4.2%（IFEval）/ 5.0%（GPQA）/ 9.2%（LCB），即这批题中"换模型能救回来"的比例本就很低 |
| D11 | 路由决策记录不完整 | 仅 IFEval auto 记录了 `lyra_selected_model`（119/119 全为 deepseek-v4-flash），其余运行未记录 |
| D12 | 时间窗 | 数据采集 2026-09-12 至 2026-09-14（UTC），价格与模型版本为该时点快照 |

---

## 8. 给 HTML 实现者的具体建议

### 布局顺序

单列纵向滚动，宽度上限 1100px。顺序：

1. **标题 + 一行口径条**（数据日期、三赛道 n、"成本为实际结算金额"）。口径条用 sticky 固定在顶部，滚动时始终可见。
2. **三句话结论**（§5 的文案模板）。不用图，就是三行大字。
3. **图 1：成本 vs 准确率散点**，三个赛道横向并列小倍数。这是首屏主图。
4. **图 2：每答对一题的成本**，横向条形，三赛道纵向堆叠分组。
5. **图 3：路由基线阶梯图**，三赛道小倍数。
6. **图 4：延迟哑铃图**，三赛道小倍数，紧跟一段"为什么慢"的说明（输出 token 数对比）。
7. **表 1：完整口径披露表**（D1–D12 逐行），默认展开，不要折叠。
8. **表 2：全量数据表**（rows 的全部字段），可以折叠。

### 每张图的轴

| 图 | x 轴 | y 轴 | 备注 |
|---|---|---|---|
| 1 | 每题成本 USD，**log10**，范围 $0.0003–$0.12，刻度 $0.001 / $0.01 / $0.1 | 准确率，**线性，0.75–1.00**，标注"轴起点非零" | 三个面板共用同一对轴范围 |
| 2 | 每答对一题成本 USD，**log10** | 类别（变体名） | 条右端标数值 |
| 3 | 类别（按成本升序：Random / auto / budget / quality / BestSingle / Oracle） | 准确率，**线性，0.75–1.00** | 柱下标成本；Oracle / Random 画水平虚线 |
| 4 | 延迟秒，**log10**，范围 4s–900s | 类别（变体名，按 p50 升序） | 哑铃两端标 p50 / p95 数值 |

散点图 y 轴从 0.75 起，形式上属于截断轴。缓解做法：(a) 用散点而非条形，点位置本身不暗示零基线；(b) 在 y 轴底部画一道明显的锯齿/断裂标记；(c) 轴标题直接写"准确率（轴起点 0.75）"。图 3 如果做成柱状图则必须改成点阵图（lollipop / dot plot），否则违反红线 1。

### 颜色编码原则

- **Fusion 三个路由用同一个色相的三个明度**（例：蓝系，auto 最浅、budget 中、quality 最深）。这样"Fusion 是一族"的视觉分组成立，且档位顺序有内在的深浅逻辑。
- **固定模型统一用中性灰**，不同模型靠**形状**区分（圆/方/三角/菱形/十字），不靠颜色。理由：一旦给五个固定模型各分一个鲜艳颜色，页面会变成八色拼盘，Fusion 的主体地位就没了。
- **基线用线不用点**：Oracle 虚线（深灰）、Random 点线（浅灰）、BestSingle 实线（中灰）。
- 不要用红绿表示好坏，会和色觉障碍冲突，也带价值判断。
- 误差线用与点同色、透明度 0.4。
- 高损耗点（error_rate > 5%）用虚线描边标记。

### 交互

【判断】要，但克制。只做三件事：

1. **hover tooltip**：显示该点的全部关键字段（accuracy 及 Wilson 区间、n_scored/n_planned、error_rate、cost_usd_per_sample、cost_per_correct_usd、p50/p95、max_tokens）。这是把高信息密度从画面移到交互里的正当做法。
2. **图例点击高亮**：点 "Fusion" 图例高亮三个路由、淡化固定模型，反之亦然。
3. **披露表锚点跳转**：每张图右上角一个小的「口径」链接，跳到表 1 对应行。

不要做：赛道切换 tab（违反红线 2，三赛道必须同屏）、动画过渡、可拖拽调参、任何需要用户操作才能看到不利数据的设计。

### 技术实现

- 纯静态单文件 HTML，数据以 JSON 内联（从 `report.json` 直接嵌），不依赖外部请求——路演现场网络不可靠。
- 图表用内联 SVG 手写或轻量库；不要引 CDN。
- 打印样式：`@media print` 保证四张图和披露表能打成 PDF，路演会有人要留档。
- 中文字体栈加 `system-ui` 兜底，数字用 `font-variant-numeric: tabular-nums`。

---

## 附：数据字段速查

`report.csv` / `report.json` 中直接可用的字段：

- 准确率与不确定性：`accuracy`、`wilson_low`、`wilson_high`、`n_planned`、`n_scored`、`n_correct`、`n_error`、`error_rate`、`error_kinds`
- 成本：`cost_usd_settled`、`cost_usd_per_sample`、`cost_usd_per_planned_sample`、`cost_usd_estimated_public`、`cost_per_correct_usd`、`cost_billed_requests`
- 效率：`latency_p50_s`、`latency_p95_s`、`latency_mean_s`、`wall_time_s`、`input_tokens_per_sample`、`output_tokens_per_sample`、`reasoning_tokens_per_sample`
- 口径：`max_tokens`、`effort`、`harness`、`is_fusion`、`started`、`completed`
- 基线（`report.json` → `baselines.<bench>`）：`best_single`、`oracle`（含 `routable_share` / `unsolved_share`）、`random_uniform`、`pareto_front`、`variants`（含 `cost_vs_best_single`、`gap_to_oracle_pp`、`headroom_captured`、`above_random_mix`）、`cost_fallback_samples`、`cost_fallback_note`
- 逐题（`samples.csv`）：`sample_id`（可做配对分析）、`score`、`lyra_selected_model`、`lyra_difficulty`、`cost_usd_matched`

## 参考链接汇总

成本-准确率前沿：
<https://artificialanalysis.ai/methodology> ·
<https://artificialanalysis.ai/evaluations/gpqa-diamond> ·
<https://artificialanalysis.ai/methodology/performance-benchmarking> ·
<https://hal.cs.princeton.edu/> ·
<https://arxiv.org/abs/2510.11977> ·
<https://epoch.ai/benchmarks> ·
<https://epoch.ai/benchmarks/about> ·
<https://crfm.stanford.edu/helm/> ·
<https://lmarena.ai/leaderboard> ·
<https://openrouter.ai/rankings>

每正确答案成本：
<https://arxiv.org/abs/2410.06992> ·
<https://www.swebench.com/>

统计与小样本：
<https://arxiv.org/abs/2411.00640> ·
<https://www.anthropic.com/research/statistical-approach-to-model-evals> ·
<https://epoch.ai/data-insights/self-reported-gpqa> ·
<https://epochai.substack.com/p/gpqa-diamond-whats-left>

路由产品论证：
<https://arxiv.org/abs/2406.18665> ·
<https://www.lmsys.org/blog/2024-07-01-routellm/> ·
<https://github.com/lm-sys/RouteLLM> ·
<https://withmartian.com/post/introducing-routerbench> ·
<https://arxiv.org/abs/2503.10657> ·
<https://arxiv.org/html/2601.07206v1> ·
<https://arxiv.org/html/2608.14641> ·
<https://openrouter.ai/openrouter/auto> ·
<https://openrouter.ai/docs/features/model-routing> ·
<https://www.notdiamond.ai/>

可视化规范与反面案例：
<https://blog.scottlogic.com/2011/09/23/a-critique-of-radar-charts.html> ·
<https://peltiertech.com/radar-plots/> ·
<https://www.lesswrong.com/posts/EWfGf8qA7ZZifEAxG/ai-benchmarking-has-a-y-axis-problem-1> ·
<https://arxiv.org/html/2508.09716v1>

工具：
<https://inspect.aisi.org.uk/log-viewer.html> ·
<https://github.com/UKGovernmentBEIS/inspect_ai>
