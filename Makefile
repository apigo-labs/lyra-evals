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
