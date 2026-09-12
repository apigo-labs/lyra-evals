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

# task | frozen id file | max_tokens (includes reasoning tokens; see roadshow design §4)
TASKS=(
  "inspect_evals/ifeval|ifeval|8192"
  "inspect_evals/gpqa_diamond|gpqa|16384"
  "inspect_tasks/livecodebench_v6.py|livecodebench|16384"
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
  IFS='|' read -r task suite max_tokens <<<"$spec"
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
    echo "=== $task | $model ${effort:+effort=$effort} | $SET ($(tr ',' '\n' <<<"$ids" | wc -l | tr -d ' ') samples)"
    # --no-fail-on-error: a sample-level failure (e.g. a dropped connection after retries) is
    # recorded as that sample's error and stays in the denominator; it must not abort the run.
    scripts/inspect_eval.sh "$task" "$model" \
      --sample-id "$ids" \
      --max-tokens "$max_tokens" \
      --max-connections 4 \
      --max-retries 1 \
      --no-fail-on-error \
      ${effort:+--reasoning-effort "$effort"} \
      "$@"
  done
done

echo "=== done; summarize with: uv run python scripts/inspect_summary.py $LOG_DIR/*.eval --csv .local/inspect-$SET.csv"
