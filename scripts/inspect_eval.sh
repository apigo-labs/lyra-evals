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
#   scripts/inspect_eval.sh inspect_tasks/livecodebench_v6.py apigo/lyra-auto --limit 10
#
# inspect_tasks/ holds repo-local Inspect tasks for benchmarks inspect_evals does not ship
# (LiveCodeBench release_v6); they reuse the frozen pack and the offline Docker grader, so
# they need `make benchmark-lcb-image` and a running Docker daemon.
#
# Credentials come from .env (VOHU_EVALS_API_KEY / VOHU_EVALS_GATEWAY_BASE_URL); nothing
# is printed. Logs go to .local/inspect-logs (git-ignored) unless INSPECT_LOG_DIR is set.
set -euo pipefail

INSPECT_AI_VERSION="${INSPECT_AI_VERSION:-0.3.263}"
INSPECT_EVALS_VERSION="${INSPECT_EVALS_VERSION:-0.20.0}"
# The IFEval scorer fork is used from a local checkout when present so that starting a run never
# depends on GitHub being reachable (a transient git failure aborted a whole matrix once).
# Clone once: git clone https://github.com/josejg/instruction_following_eval .local/upstream/instruction_following_eval
_LOCAL_IFEVAL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.local/upstream/instruction_following_eval"
if [[ -z "${IFEVAL_SCORER_SRC:-}" && -f "$_LOCAL_IFEVAL/pyproject.toml" ]]; then
  IFEVAL_SCORER_SRC="$_LOCAL_IFEVAL"
fi
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

# Repo-local tasks (inspect_tasks/...) need vohu_evals and the inspect_tasks package inside
# the isolated env. They are reached through PYTHONPATH rather than `--with-editable .`:
# installing this project would add src/instruction_following_eval (the frozen Google
# original) to the env and shadow the josejg fork that inspect_evals' IFEval scorer imports
# under the same name. PYTHONPATH is set only for these task invocations, and the wrapper
# modules import nothing beyond the standard library.
if [[ "$TASK" == inspect_tasks/* ]]; then
  export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi

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
