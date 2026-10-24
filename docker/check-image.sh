#!/bin/sh
# Fail if a built image carries anything that must never be shipped:
# env files, databases, ride files, VCS data, or secret-looking ENV entries.
#
#   docker/check-image.sh <image>
#
# Runs in CI before every push, and is the check to run after a local build.
# It inspects the image itself, not the build context, so it also catches a
# file created *during* the build (a migration run as a RUN layer, say).
set -eu

image="${1:?usage: $0 <image>}"
status=0

echo "check-image: scanning $image"

# Files. Only the trees the build writes to are scanned: the base image's own
# files (CA bundles in /etc/ssl and the like) are not ours to judge. The
# installed dependency trees are skipped — packages legitimately ship test
# fixtures with these names.
found=$(docker run --rm --network none --entrypoint sh "$image" -c '
  find /app /home \
    \( -path /app/.venv -o -path /app/.web/node_modules \) -prune -o \
    \( -name ".env" -o -name ".env.*" -o -name "*.db" -o -name "*.sqlite" -o -name "*.sqlite3" \
       -o -name "*.fit" -o -name ".git" -o -name "id_rsa*" -o -name "id_ed25519*" -o -name "*.key" \) \
    ! -name ".env.example" -print 2>/dev/null
  # The MCP image has no data directory at all; its absence is a pass.
  find /app/data -mindepth 1 -print 2>/dev/null || true
')
if [ -n "$found" ]; then
  echo "check-image: FAIL — files that must not be in the image:"
  echo "$found" | sort -u | sed 's/^/  /'
  status=1
fi

# Baked-in environment. ENV and ARG values are stored in the image config and
# readable by anyone who can pull it.
env_hits=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
  | grep -iE '^[^=]*(secret|token|password|passwd|api_key|private)[^=]*=' || true)
if [ -n "$env_hits" ]; then
  echo "check-image: FAIL — secret-looking ENV baked into the image:"
  echo "$env_hits" | sed 's/=.*/=<redacted>/; s/^/  /'
  status=1
fi

[ "$status" -eq 0 ] && echo "check-image: OK"
exit "$status"
