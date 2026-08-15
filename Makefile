.PHONY: sync format lint test context-kg secret-scan verify

sync:
	uv sync --all-groups

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .

test:
	uv run pytest --cov=vohu_evals --cov-report=term-missing

context-kg:
	uv run python scripts/validate_context_kg.py context-kg

secret-scan:
	uv run python scripts/secret_scan.py .

verify: lint test context-kg secret-scan
	git diff --check
