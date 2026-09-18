# 数据说明

本目录是 2026-09 Fusion 路演主测试的原始报告产物，从 `.local/inspect-report-main/` 原样复制，未做任何裁剪或修改，
用于让 `../RESULTS.md` 的每个数字可复算。

- **跑批时间**：2026-09-12T16:06 至 2026-09-14T22:41（UTC）
- **报告生成时间**：2026-09-15
- **覆盖范围**：3 赛道 × 8 变体 = 24 组。赛道为 IFEval（120 题）、GPQA Diamond（60 题）、LiveCodeBench v6（120 题）；
  变体为 5 个固定模型（gpt-5.6-luna、gpt-5.6-sol、gpt-6-astra、claude-sonnet-5、claude-opus-5）
  加 Fusion 三档路由（apigo/lyra-auto、apigo/lyra-budget、apigo/lyra-quality）。

## 生成命令

```
make inspect-report \
  LOGS=.local/inspect-logs/main \
  PLATFORM=.local/platform-logs-main.json \
  OUT=.local/inspect-report-main
```

底层是 `scripts/inspect_report.py`：读 Inspect 的 `.eval` 日志目录得到逐题分数与 token，读平台账单导出
（`make platform-logs-export`）按请求时长匹配逐题成本，输出 `report.csv` / `report.json` / `report.html` /
`samples.csv` / `runs.jsonl`。本目录只收了前三个。

## report.csv

每行一个 (赛道 × 变体) 组，共 24 行；表尾另有 9 行路由基线（`model` 以 `baseline:` 开头，只有 `accuracy` 和
`cost_usd_per_sample` 两列有值）。

| 字段 | 含义 |
|---|---|
| `benchmark` / `benchmark_label` | 赛道 id 与中文名 |
| `model` / `variant_label` | 变体 id 与展示名 |
| `effort` | reasoning effort，固定模型为 `high`，Fusion 不适用 |
| `max_tokens` | 请求的输出上限。Fusion 在 GPQA/LCB 用 32768，固定模型用 16384，IFEval 两侧均为 8192 |
| `harness` | 调用路径，本次全部为 `direct`（direct-api） |
| `is_fusion` | 是否为 Fusion 路由变体 |
| `n_planned` | 计划题数 |
| `n_scored` | 成功评出分的题数（accuracy 与成本的分母） |
| `n_correct` | 答对题数 |
| `n_error` | 报错未产生答案的题数 |
| `n_missing` | 日志里完全没有记录的题数 |
| `error_rate` | `(n_planned - n_scored) / n_planned` |
| `error_kinds` | 失败类型计数 JSON：`connection` / `upstream` / `scorer` / `missing` / `other` |
| `accuracy` | `n_correct / n_scored` |
| `wilson_low` / `wilson_high` | 按 `n_scored` 计算的 Wilson 95% 区间 |
| `cost_usd_settled` | 该 run 的平台结算总额 |
| `cost_billed_requests` | 产生账单的请求数（可能多于 `n_scored`，失败请求也可能计费） |
| `cost_usd_per_sample` | `cost_usd_settled / n_scored` |
| `cost_usd_per_planned_sample` | `cost_usd_settled / n_planned`（旧口径，对照用） |
| `cost_usd_estimated_public` | 按公开价目表估算的成本，Fusion 无此值 |
| `cost_per_correct_usd` | `cost_usd_settled / n_correct` |
| `latency_p50_s` / `latency_p95_s` / `latency_mean_s` | 逐题端到端耗时分位数与均值，秒 |
| `wall_time_s` | 该组跑批墙钟时长 |
| `input_tokens_per_sample` / `output_tokens_per_sample` / `reasoning_tokens_per_sample` | 每题平均 token |
| `routing_selected_models` | Fusion 实际选中的下游模型计数 JSON。仅 IFEval 的 lyra-auto 有值，其余组上游未回传 |
| `started` / `completed` | 该组起止时间（UTC） |

## report.json

两个顶层键：

- `rows`：与 `report.csv` 的 24 个数据行同构（不含基线行）。
- `baselines`：按赛道分组，每组含
  - `pool`：参与基线计算的固定模型列表
  - `n_planned` / `n_evaluable`：计划题数 / 至少一个固定模型评出分的题数（基线的分母）
  - `best_single`：准确率最高的单个固定模型及其每题成本
  - `oracle`：每题取「答对的模型里最便宜的那次」，含 `routable_share`（可路由题占比）与 `unsolved_share`（无人答对占比）
  - `random_uniform`：五个固定模型准确率与成本的均匀平均
  - `pareto_front`：成本-准确率帕累托前沿上的点
  - `variants`：三档 Fusion 的派生指标——`cost_vs_best_single`、`headroom_captured`（相对帕累托前沿捕获的准确率空间比例，
    负值表示落在前沿之下）、`gap_to_oracle_pp`、`above_random_mix`
  - `cost_fallback_samples`：逐题成本匹配不到账单、回退到运行平均的记录数。LCB 有 124 条，该赛道的基线成本精度较低

## samples.csv

逐题明细，2400 行（24 组 × 各自题数）。

关键字段：`benchmark`、`variant_label`、`sample_id`、`score`（0/1）、`score_detail`、`error`、
`input_tokens` / `output_tokens` / `reasoning_tokens`、`total_time_s`、`stop_reason`、`served_model`、
`cost_usd_matched`（按请求时长匹配到的逐题账单金额，空值表示回退到运行平均）、`cost_match_duration_delta_s`、
`lyra_selected_model` / `lyra_router_model` / `lyra_mode` / `lyra_difficulty` / `lyra_task_type` /
`lyra_policy_version` / `lyra_attempt_count`（仅 Fusion 组可能有值，本次只有 IFEval 的 lyra-auto 回传了）。

**不含题面和模型原文**。`sample_id` 为数据集自带的题目标识（IFEval 为序号、GPQA 为 `recXXXXXXXX` 记录 id、
LCB 为公开赛题编号如 `1873_B`），不可由此还原 GPQA 题面。提交前已跑过 `uv run python scripts/secret_scan.py`，
并确认三个文件不含 key、token 或邮箱。
