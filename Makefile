.PHONY: sync format lint test context-kg secret-scan verify

sync:
	uv sync --all-groups

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .

test:
	uv run python -m pytest --cov=vohu_evals --cov-report=term-missing

.PHONY: console-install console-build console console-dev console-smoke-image console-acp-image
console-install:
	uv sync --all-groups --extra console
	cd console && npm ci

console-build:
	cd console && npm run build

console: console-build
	uv run --extra console python -m uvicorn vohu_evals.console.server:app --host 127.0.0.1 --port 8768

console-dev:
	cd console && npm run dev

console-smoke-image:
	docker build -t lyra-evals-smoke:local deploy/console/smoke

console-acp-image:
	docker build -t lyra-evals-claude-acp:local deploy/console/claude-acp

context-kg:
	uv run python scripts/validate_context_kg.py context-kg

secret-scan:
	uv run python scripts/secret_scan.py .

verify: lint test context-kg secret-scan
	git diff --check

.PHONY: console-codex-image
console-codex-image:
	docker build -t lyra-evals-codex-acp:local deploy/console/codex-acp

.PHONY: benchmark-prepare benchmark-runtimes benchmark-lcb-image
benchmark-runtimes:
	uv run --extra benchmarks python scripts/prepare_benchmark_runtimes.py

benchmark-prepare:
	uv run --extra benchmarks python scripts/prepare_benchmarks.py ifeval gpqa livecodebench tau2 swebench

benchmark-lcb-image:
	docker build -t lyra-evals-lcb-grader:local deploy/suites/livecodebench

.PHONY: console-gateway-image
console-gateway-image:
	docker build -t lyra-evals-gateway:local deploy/console/gateway

.PHONY: benchmark-tau-image benchmark-tau-check
benchmark-tau-image:
	uv run --extra console python scripts/build_tau_runtime.py

benchmark-tau-check:
	uv run --extra console python scripts/verify_tau_runtime.py

.PHONY: inspect-eval inspect-summary
# Direct-api profile via Inspect AI in an isolated tool env. Example:
#   make inspect-eval TASK=inspect_evals/ifeval MODEL=apigo/lyra-auto ARGS="--limit 10"
inspect-eval:
	scripts/inspect_eval.sh $(TASK) $(MODEL) $(ARGS)

inspect-summary:
	uv run python scripts/inspect_summary.py .local/inspect-logs/*.eval $(ARGS)

.PHONY: calibration
# Stage-4 calibration (7 variants x 3 benchmarks x 10 frozen samples) via Inspect; SET=main for the main sets.
calibration:
	scripts/run_calibration.sh

.PHONY: inspect-report platform-logs-export
# Standalone cost / latency / accuracy comparison report from one Inspect run set.
#   make inspect-report LOGS=.local/inspect-logs/calibration PLATFORM=.local/platform-logs-today.json OUT=.local/inspect-report
LOGS ?= .local/inspect-logs/calibration
PLATFORM ?= .local/platform-logs-today.json
OUT ?= .local/inspect-report
inspect-report:
	uv run python scripts/inspect_report.py --logs $(LOGS) --platform $(PLATFORM) --out $(OUT)

# Refresh the Platform billing export used for cost attribution (read-only API call).
#   make platform-logs-export PLATFORM_ARGS="--time custom --from 2026-09-12T00:00:00+00:00 --to 2026-09-13T00:00:00+00:00"
platform-logs-export:
	uv run python scripts/platform_logs_export.py --out $(PLATFORM) $(PLATFORM_ARGS)
