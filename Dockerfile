FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev
COPY src ./src
COPY benchmarks ./benchmarks
COPY configs ./configs
COPY targets ./targets
COPY schemas ./schemas
COPY prompts ./prompts
ENTRYPOINT ["uv", "run", "--frozen", "vohu-eval"]
