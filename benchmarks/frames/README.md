# FRAMES Development

该插件只用于 VOHU 通用联网多跳能力开发，不作为对外主分数。题库从 Google 官方 Hugging Face
revision `58d9fb6330f3ab1316d1eca12e5e8ef23dcc22ef` 下载到 `.cache/`，不进入 Git。

- `smoke`：冻结 dev 的前 20 题；
- `calibration`：冻结 dev 100 题；
- `publication`：名称沿用 runner stage，但实际是隔离 holdout 100 题，不代表对外发布。

划分只由 `sha256(seed:index)` 排序决定。模型请求只包含 `Prompt`，绝不包含 `Answer`、Wikipedia gold links
或 reasoning label。BrowseComp 的题目、答案和逐题结果不进入本开发集流程。
