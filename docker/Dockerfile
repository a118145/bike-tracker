# syntax=docker/dockerfile:1
#
# Production image for a Reflex app: one process, one port, no secrets.
#
# Build from the repo root so the context is the project, not docker/:
#   docker build -f docker/Dockerfile -t bike-tracker .
#
# The build context is filtered by docker/Dockerfile.dockerignore, an
# allow-list. Nothing reaches the image unless it is named there, so a new
# `.env`, database or token file is excluded without anyone remembering to.
#
# Runtime configuration (Hammerhead credentials etc.) comes from the
# container's environment, set by compose / Ansible on the server. The build
# itself needs no secrets — do not add any as ARG or ENV: both are stored in
# the image metadata and readable with `docker history`.

ARG PYTHON_VERSION=3.12

# Pinned tool images. Copying binaries out of them replaces `curl | bash`
# from a third-party apt repo.
#
# Keep BUN_VERSION equal to `Bun.VERSION` in reflex_base/constants/installer.py
# for the installed Reflex. Without a system bun, Reflex downloads one at build
# time by piping an install script from its GitHub *main* branch into bash.
ARG BUN_VERSION=1.3.14
FROM node:22-slim AS node
FROM oven/bun:${BUN_VERSION}-slim AS bun
FROM ghcr.io/astral-sh/uv:0.11 AS uv

FROM python:${PYTHON_VERSION}-slim

# Reflex 0.9 needs Node >= 22.12 to build the frontend and bun to install the
# JS packages (REFLEX_USE_SYSTEM_BUN below makes it use this one).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=uv /uv /usr/local/bin/uv

RUN useradd --create-home --home-dir /home/reflex --uid 1000 reflex \
    && mkdir -p /app/data \
    && chown -R reflex:reflex /app

WORKDIR /app
USER reflex

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    # No download caches in the image: uv's and bun's together are ~640 MB.
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}" \
    REFLEX_TELEMETRY_ENABLED=false \
    REFLEX_USE_SYSTEM_BUN=true \
    # Prod mode prerenders every page in Node by default, which imports
    # browser-only libraries there: plotly.js dies with "self is not defined".
    # Prerendering buys first-paint and SEO, neither of which a LAN app needs.
    REFLEX_SSR=false \
    # Absolute path: a relative sqlite URL depends on the working directory.
    # /app/data is the one volume; it holds the database and the FIT drop
    # folder (data/inbox).
    REFLEX_DB_URL=sqlite:////app/data/reflex.db

# Dependencies first, in their own layer, so a code change does not
# reinstall them. --frozen: install exactly uv.lock, never re-resolve.
COPY --chown=reflex:reflex pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=reflex:reflex . .

# Compile the app and build the frontend now, so the JS packages and bun are
# baked into the image instead of being downloaded on the homeserver at every
# start. The app imports its models during compile, so point it at a
# throwaway database — the real one lives on the volume and must never be
# created in the image.
RUN REFLEX_DB_URL=sqlite:////tmp/build.db BUN_INSTALL_CACHE_DIR=/tmp/bun-cache \
        reflex export --frontend-only --no-zip \
    && rm -rf /tmp/build.db /tmp/bun-cache

# The release this image is, reported by GET /api/v1/version so the MCP layer
# can see what is deployed. CI passes the tag; a local build says "dev". Not a
# secret — the one kind of value an ARG may carry here. Declared this late so a
# new version does not invalidate the dependency and frontend layers above.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

EXPOSE 3000

# /_health checks the database as well as the process. start-period covers the
# migration and the frontend compile that `reflex run --env prod` performs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:3000/_health', timeout=4).status != 200)"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
