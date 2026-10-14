# mcp-flowise is a **stdio** MCP server: it speaks MCP JSON-RPC on stdin/stdout
# and diagnostics go to stderr. It therefore exposes no HTTP port, and it must
# be attached to an MCP client, not published.
#
# The image runs the checkout that was COPYied in - no `git clone`/`uvx --from
# git+...` at start-up, and no credentials baked into the image. Pass
# FLOWISE_API_KEY and FLOWISE_API_ENDPOINT through `docker run -e ...` or your
# orchestrator.

FROM python:3.12-slim AS base

# uv provides `uv sync --frozen`, which installs exactly what uv.lock pins.
COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /bin/

WORKDIR /app

# Dependency metadata first so dependency installation stays cached.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself, installed from this checkout.
COPY mcp_flowise ./mcp_flowise
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Stdout is the MCP transport; nothing else may write there.
ENTRYPOINT ["mcp-flowise"]
CMD []
