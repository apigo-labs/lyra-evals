#!/usr/bin/env bash
# Stage-4 calibration of the Fusion roadshow matrix: 7 variants x 3 benchmarks x 10 frozen
# calibration samples, all through the Inspect direct-api profile. Purpose is protocol,
# output-cap, ledger and latency checks only; these numbers must not be reported as accuracy.
#
# Prerequisites: .env filled, `make benchmark-lcb-image`, Docker running, NLTK punkt data
# present (IFEval scorer), and frozen sample sets in .local/inspect-samples/ (created by
# scripts/freeze_inspect_samples.py). Set SET=main to run the main test sets instead.
#
# Usage:  scripts/run_calibration.sh            # all variants, calibration set
#         SET=main scripts/run_calibration.sh   # main test set (120/60/120)
#         VARIANTS="apigo/lyra-auto" scripts/run_calibration.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SET="${SET:-calibration}"
SAMPLES=".local/inspect-samples"
LOG_DIR="${INSPECT_LOG_DIR:-.local/inspect-logs/$SET}"
export INSPECT_LOG_DIR="$LOG_DIR"

# "<model>[:<reasoning-effort>]" — effort omitted means provider default (Fusion tiers are
# routing modes, not efforts, so they never carry one).
DEFAULT_VARIANTS="gpt-5.6-luna:high gpt-5.6-sol:high gpt-6-astra:high claude-sonnet-5:high claude-opus-5:high apigo/lyra-auto apigo/lyra-budget apigo/lyra-quality"
VARIANTS="${VARIANTS:-$DEFAULT_VARIANTS}"

# task | frozen id file | max_tokens for fixed models | max_tokens for the apigo/lyra-* routes
#
# Both caps include billed reasoning output. The fixed-model column is the roadshow design §4
# value (8192 / 16384 / 16384) and is deliberately left untouched, so those cells stay
# comparable with every run made so far.
#
# The Fusion routes get a wider cap on GPQA and LCB because 16384 was cutting them off
# mid-reasoning: Lyra spends the whole budget thinking, the answer body comes back empty, and
# the gateway reports that as `lyra_execution_failed`. Evidence behind the 32768:
#   * Platform billing for 29 failed requests shows output = 16384 tokens with all of it
#     counted as reasoning; our 11 high-confidence request-id matches sit at 16384-16452.
#   * In the 16384 main run the failures cluster by wall time exactly where the budget runs
#     out: lyra-auto / lyra-budget fail at 136-189s, i.e. ~16.4k tokens at ~100 tok/s.
#   * Replaying, at 32768, the 12 GPQA prompts that lyra-auto failed on at 16384: 7 come back
#     with a scorable body. All six probe cells (3 routes x GPQA/LCB) returned
#     finish_reason=stop at 32768.
#   * Re-running the six affected main-set groups at 32768 then confirmed it end to end. Errors
#     per group, 16384 -> 32768: GPQA 12->5, 7->4, 4->1 (auto / budget / quality); LCB 18->14,
#     22->15, 21->9. 84 lost samples across the six became 48.
# And the reason not to go higher than 32768: the upstream attempt also has a hard deadline of
# its own at ~297s. Re-running the 6 prompts that still failed at 32768 with the cap at 65536
# produced failures at 297.5-297.6s — the same instant as at 32768, not the ~600s a 65536-token
# budget would take to burn. Past ~32768 the cap is simply no longer the binding constraint, so
# a larger number buys nothing and only widens the tail on cost and latency.
#
# IFEval keeps 8192 for every variant: it needs almost no reasoning, and all three Fusion
# routes finished the 8192 main run with 0-1 errors, so there is nothing there to buy.
#
# This makes the Fusion cells run under a different output cap than the fixed models — they are
# not directly comparable on cost or on the latency tail. That asymmetry is not allowed to stay
# implicit: scripts/inspect_report.py reads max_tokens out of each log header and prints it as a
# column in the per-benchmark table, with a note above it.
TASKS=(
  "inspect_evals/ifeval|ifeval|8192|8192"
  "inspect_evals/gpqa_diamond|gpqa|16384|32768"
  "inspect_tasks/livecodebench_v6.py|livecodebench|16384|32768"
)

# Completed (task, model) pairs already in LOG_DIR are skipped so an interrupted matrix can be
# resumed without re-spending. A run counts as done only when its log status is "success".
done_pairs="$(python3 - "$LOG_DIR" <<'PY'
import glob, json, subprocess, sys
pairs = set()
for path in sorted(glob.glob(f"{sys.argv[1]}/*.eval")):
    try:
        raw = subprocess.run(["uvx", "--from", "inspect-ai==0.3.263", "inspect", "log", "dump", "--header-only", path],
                             check=True, capture_output=True, text=True).stdout
        head = json.loads(raw)
    except Exception:
        continue
    if head.get("status") != "success":
        continue
    task = head["eval"]["task"].split("/")[-1].removesuffix(".py")
    model = head["eval"]["model"].removeprefix("openai-api/apigo/")
    pairs.add(f"{task}|{model}")
print("\n".join(sorted(pairs)))
PY
)"

for spec in "${TASKS[@]}"; do
  IFS='|' read -r task suite max_tokens fusion_max_tokens <<<"$spec"
  ids_file="$SAMPLES/$suite.$SET.txt"
  [[ -f "$ids_file" ]] || { echo "missing $ids_file (run scripts/freeze_inspect_samples.py)" >&2; exit 2; }
  ids="$(tr -d '[:space:]' <"$ids_file")"
  task_key="$(basename "$task" .py)"
  for variant in $VARIANTS; do
    model="${variant%%:*}"
    effort=""
    [[ "$variant" == *:* ]] && effort="${variant##*:}"
    if grep -qx "$task_key|$model" <<<"$done_pairs"; then
      echo "=== skip (already complete in $LOG_DIR): $task | $model"
      continue
    fi
    # Only the three apigo/lyra-* routes take the wider cap; everything else keeps the design §4
    # value. Matching on the prefix rather than listing the three ids keeps a future Fusion mode
    # from silently falling back to 16384 and reproducing the empty-body failures.
    tokens="$max_tokens"
    [[ "$model" == apigo/lyra-* ]] && tokens="$fusion_max_tokens"
    echo "=== $task | $model ${effort:+effort=$effort} | $SET ($(tr ',' '\n' <<<"$ids" | wc -l | tr -d ' ') samples, max_tokens=$tokens)"
    # Streaming is not set here: every request goes through scripts/inspect_eval.sh, which
    # injects -M stream=true (see the STREAMING block there — streaming is what makes upstream
    # failures legible). INSPECT_STREAM=false turns it off for a whole matrix.
    # --no-fail-on-error: a sample-level failure (e.g. a dropped connection after retries) is
    # recorded as that sample's error and stays in the denominator; it must not abort the run.
    scripts/inspect_eval.sh "$task" "$model" \
      --sample-id "$ids" \
      --max-tokens "$tokens" \
      --max-connections 4 \
      --max-retries 1 \
      --no-fail-on-error \
      ${effort:+--reasoning-effort "$effort"} \
      "$@"
  done
done

echo "=== done; summarize with: uv run python scripts/inspect_summary.py $LOG_DIR/*.eval --csv .local/inspect-$SET.csv"
