#!/usr/bin/env bash
# Run an Inspect AI task against the APIGO Gateway (OpenAI Chat Completions) in an
# isolated tool environment. This is the "direct-api" profile: one request per sample,
# no agent, no tools, official inspect_evals scorers.
#
# Inspect lives outside the project venv on purpose: inspect_evals' IFEval scorer needs
# the josejg `instruction_following_eval` fork, whose import name collides with the
# frozen Google package vendored under src/.
#
# Usage:
#   scripts/inspect_eval.sh <task> <model-id> [extra inspect args...]
#   scripts/inspect_eval.sh inspect_evals/ifeval apigo/lyra-auto --limit 10
#   scripts/inspect_eval.sh inspect_evals/gpqa_diamond gpt-5.6-luna --reasoning-effort high --limit 10
#
# Credentials come from .env (VOHU_EVALS_API_KEY / VOHU_EVALS_GATEWAY_BASE_URL); nothing
# is printed. Logs go to .local/inspect-logs (git-ignored) unless INSPECT_LOG_DIR is set.
set -euo pipefail

INSPECT_AI_VERSION="${INSPECT_AI_VERSION:-0.3.263}"
INSPECT_EVALS_VERSION="${INSPECT_EVALS_VERSION:-0.20.0}"
IFEVAL_SCORER_SRC="${IFEVAL_SCORER_SRC:-git+https://github.com/josejg/instruction_following_eval}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <task> <model-id> [inspect args...]" >&2
  exit 2
fi
TASK="$1"; MODEL="$2"; shift 2

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
: "${VOHU_EVALS_API_KEY:?VOHU_EVALS_API_KEY missing (set it in .env)}"
export APIGO_API_KEY="$VOHU_EVALS_API_KEY"
export APIGO_BASE_URL="${VOHU_EVALS_GATEWAY_BASE_URL:-https://api.apigo.ai}"
APIGO_BASE_URL="${APIGO_BASE_URL%/}"
[[ "$APIGO_BASE_URL" == */v1 ]] || APIGO_BASE_URL="$APIGO_BASE_URL/v1"
export APIGO_BASE_URL

LOG_DIR="${INSPECT_LOG_DIR:-.local/inspect-logs}"
mkdir -p "$LOG_DIR"

exec uvx --from "inspect-ai==$INSPECT_AI_VERSION" \
  --with "inspect-evals==$INSPECT_EVALS_VERSION" \
  --with openai \
  --with "$IFEVAL_SCORER_SRC" \
  inspect eval "$TASK" \
  --model "openai-api/apigo/$MODEL" \
  --epochs 1 \
  --log-dir "$LOG_DIR" \
  --display plain \
  "$@"
