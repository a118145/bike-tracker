#!/bin/sh
# Container start: migrate, then hand the process over to Reflex.
#
# - Migrations run here, not in a Dockerfile RUN layer, because only now is
#   the real database (on the /app/data volume) present.
# - `set -e`: a failed migration stops the container, and the log says why.
#   Starting the app against a half-migrated schema is worse than not starting.
# - `exec`: Reflex replaces this shell as the container's main process, so it
#   receives SIGTERM on `docker stop` and the container dies with it — which is
#   what makes `restart: unless-stopped` and the healthcheck meaningful.
set -eu

echo "entrypoint: applying migrations"
reflex db migrate

echo "entrypoint: starting reflex on :3000"
exec reflex run --env prod --single-port --backend-host 0.0.0.0 --backend-port 3000 --frontend-port 3000
