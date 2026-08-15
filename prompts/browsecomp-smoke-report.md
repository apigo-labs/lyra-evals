你正在为 VOHU 团队撰写一份中文 BrowseComp 内部 Smoke 测试报告，不是对外宣传稿。

只读取 `{evidence_dir}` 下冻结的证据文件。不要联网、调用其他模型、修改证据、重算分数或补造缺失值。
报告必须包含：标题、结论摘要、方法、VOHU 结果、稳定性与成本、按评测赛道逐行的外部公开参照、发布判断和下一步。

BrowseComp 表的列至少包括冻结证据 `protocol.profile` 指定的 VOHU Research 版本、Claude Opus 5、Claude Fable 5、GPT-5.6 Sol 与
OpenRouter Fusion。没有 BrowseComp 官方成绩的系统写 `—`，不得拿 HLE 或 DRACO 分数填入 BrowseComp。
另用简短的“其他赛道参照”表列出 Fusion 的 DRACO、GPT 的 GPQA 和 Claude/Fable 的 HLE，明确它们不能与
VOHU BrowseComp 排名。厂商分数只能称为历史官方公开参照；VOHU 是固定 20 题 Smoke，且工具、日期、采样和
系统失败口径没有与厂商同日重跑，因此不能推导强弱。

每个包含数字、布尔值或比较性结论的行都必须在行末添加对应证据标记：
`<!-- evidence:path.to.value -->`。同一行包含多个数字时，每个数字后立即放置各自标记。标记引用的 JSON
primitive 必须以完全相同的字面值出现在同一行。
证据路径必须从 `report-summary.json` 的 JSON 根开始，例如 `metrics.score_percent`；不得添加
`report-summary.` 文件名前缀。模型名中的版本数字和表头中的数字也算数字，必须在同一行添加相应证据标记。
如果 evidence 含 `derivation.type=rescore`，`metrics.total_cost_usd` 和 `metrics.inherited_target_cost_usd` 都只表示
继承的 VOHU target 成本，不包含本次 evaluator 成本；不得写成 target 与 evaluator 的合计总成本。否则
`metrics.total_cost_usd` 表示本次 run 已审计的 VOHU target 成本，仍不包含 evaluator 成本，不得称为“继承成本”。

如果 `publishable` 为 false，标题和摘要必须明确写“内部验证稿、不可对外宣传”，并在同一行写出
`publishable=false`。保留所有 system failure 于固定分母，不得只报告 completed 子集准确率。不要使用“领先”、
“超过”、“击败”或任何可被理解为同榜比较的措辞。

只返回 Markdown。
