# syntax=docker/dockerfile:1
#
# The MCP server image: Python, FastMCP and httpx — no Reflex, no Node, no
# database. It is the Cloudflare tunnel's origin, so it holds as little as
# possible (brief §3.4): its only reach is the app's REST API over the Docker
# network.
#
# Build with this folder as the context:
#   docker build -f mcp_server/Dockerfile -t bike-tracker-mcp mcp_server
#
# The context is filtered by Dockerfile.dockerignore, an allow-list. Secrets
# come from the container's environment at run time (MCP_BEARER_TOKEN,
# BIKE_TRACKER_API_TOKEN) and must never be an ARG or ENV here: both end up in
# the image metadata, readable with `docker history`.

ARG PYTHON_VERSION=3.12
FROM ghcr.io/astral-sh/uv:0.11 AS uv

# --- dependencies, in a stage of their own so uv stays out of the image ------
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --frozen: exactly uv.lock, never re-resolved.
RUN uv sync --frozen --no-dev --no-install-project

# --- runtime -----------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim

RUN useradd --create-home --home-dir /home/mcp --uid 1000 mcp
WORKDIR /app
COPY --from=build --chown=mcp:mcp /app/.venv /app/.venv
COPY --chown=mcp:mcp bike_tracker_mcp ./bike_tracker_mcp
USER mcp

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

# /healthz answers without the token and returns nothing but "ok".
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status != 200)"]

# Run as a module, never as a file path: a script directory on sys.path is how
# a file named like an installed package shadows it.
ENTRYPOINT ["python", "-m", "bike_tracker_mcp.training_mcp"]
