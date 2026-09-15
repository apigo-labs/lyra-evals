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
#
# Requests are streamed by default (-M stream=true). See the STREAMING block below for why
# and for how to turn it off (INSPECT_STREAM=false).
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

# STREAMING — on by default.
#
# The reason is diagnostic, not a timeout workaround. Non-streamed runs reported failures as
# bare APIConnectionError, which reads like a network fault and hides what actually went
# wrong; the same samples run streamed report `lyra_execution_failed` from the SSE body, and
# that pointed at the real cause (the output budget being spent entirely on reasoning tokens,
# leaving no answer — see the TASKS table in run_calibration.sh). Error counts barely moved
# when streaming was turned on; what changed is that the failures became legible.
#
# What streaming does NOT do, despite an earlier theory in this repo's notes: it does not
# defeat a connection idle timeout, because there is no idle timeout to defeat. The gateway
# emits an SSE heartbeat every 15.0s (measured on raw bytes), so a connection carrying a long
# reasoning turn is never idle in the first place. The failures that cluster at 730-733s
# (livecodebench x claude-sonnet-5, effort=high) reproduce identically with streaming on,
# heartbeats flowing, and a 3600s client timeout — cause still unknown, tracked upstream.
#
# One caveat worth knowing when reading logs: reasoning models emit nothing while they think,
# so time-to-first-chunk can be far out (gpt-6-astra at effort=high: 50.4s) and some Lyra
# routes buffer the whole answer into a handful of chunks. A quiet stream is not a stalled one.
#
# Inspect's openai-api provider (OpenAICompatibleAPI) defaults to non-streaming:
# should_stream() returns False and we pass no on_stream callback, so resolve_stream() only
# streams when the `stream` model arg is set explicitly. Hence -M stream=true.
#
# Overrides:
#   INSPECT_STREAM=false scripts/inspect_eval.sh ...     # force non-streaming
#   scripts/inspect_eval.sh <task> <model> -M stream=false   # same, per-invocation
# The caller's own -M stream=... wins and suppresses the injection below. That check is
# belt-and-braces rather than load-bearing: parse_cli_args() folds -M into a dict in
# argument order, so a later -M stream=... simply overwrites an earlier one (verified by
# running with both -M stream=true -M stream=false: no error, the logged request carried no
# "stream" field). Skipping the injection just keeps the recorded model_args clean.
# (Expanded below as ${STREAM_ARGS[@]+"..."}: under `set -u` the stock macOS bash 3.2 treats
# an empty array as unset and would abort on a plain "${STREAM_ARGS[@]}".)
STREAM_ARGS=(-M "stream=${INSPECT_STREAM:-true}")
for _arg in "$@"; do
  case "$_arg" in
    stream=*|-Mstream=*|--model-arg=stream=*) STREAM_ARGS=() ;;
  esac
done

exec uvx --from "inspect-ai==$INSPECT_AI_VERSION" \
  --with "inspect-evals==$INSPECT_EVALS_VERSION" \
  --with openai \
  --with "$IFEVAL_SCORER_SRC" \
  inspect eval "$TASK" \
  --model "openai-api/apigo/$MODEL" \
  --epochs 1 \
  --log-dir "$LOG_DIR" \
  --display plain \
  ${STREAM_ARGS[@]+"${STREAM_ARGS[@]}"} \
  "$@"
