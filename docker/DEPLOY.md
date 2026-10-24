# Deploying bike-tracker

How a release gets from this repo to the homeserver:

```
git tag vX.Y.Z → GitHub Actions: build → leak check → smoke test → push to Docker Hub
                                                                       ↓
                               rerun Ansible playbook → homeserver pulls :latest
```

A push to `main` runs the same build and checks but **publishes nothing**.
Only a `v*` tag releases.

## One-time setup

- [ ] **1. GitHub secrets.** Repo → Settings → Secrets and variables → Actions → *New repository secret*:
  - `DOCKERHUB_USERNAME`: your Docker Hub user name
  - `DOCKERHUB_TOKEN`: the token from step 3
  - `DOCKERHUB_REPO`: `<dockerhub-user>/bike-tracker` (lowercase)
- [ ] **2. Docker Hub repository.** hub.docker.com → *Create repository*. Name it like the GitHub repo (`bike-tracker`), visibility **Public**.
- [ ] **3. Docker Hub access token.** Account settings → Personal access tokens → *Generate new token*. Access permissions: **Read, Write, Delete**. Delete is required for the README sync to the repository description. Copy the value into `DOCKERHUB_TOKEN` right away, because it is shown only once.
- [ ] **4. First release.** `git tag v0.1.0 && git push origin v0.1.0`, then watch the *Actions* tab. Afterwards Docker Hub shows `0.1.0`, `0.1`, `latest` and `sha-…`.
- [ ] **5. Homeserver (Ansible).** Add the service from `docker/compose.example.yml` to the playbook:
  - env file `/opt/bike-tracker/app.env`, rendered from the vault, mode `0600`. Its keys are those in `.env.example`.
  - data directory `/opt/bike-tracker/data`, owner uid/gid `1000`
  - `pull: always` on the compose task, so a rerun fetches the new `:latest`:
    ```yaml
    - name: bike-tracker
      community.docker.docker_compose_v2:
        project_src: /opt/bike-tracker
        pull: always
        state: present
    ```
- [ ] **6. Backup.** Set up a backup of `/opt/bike-tracker/data` before the first real import.
- [ ] **7. Hammerhead redirect URI.** In the production env file, set `HAMMERHEAD_REDIRECT_URI=http://<homeserver-lan-ip>:3000/settings/hammerhead/callback`. Register exactly that string in the Hammerhead API settings: OAuth compares it byte for byte.

## Every release

1. Merge to `main`, then wait for the build to go green.
2. `git tag vX.Y.Z && git push origin vX.Y.Z`
3. Rerun the playbook.

**Rollback:** pin the image to the previous version (`…/bike-tracker:0.1.0`) in the playbook and rerun it. Migrations only go forward, so check `alembic/versions` before rolling back across a schema change.

## What never leaves the laptop

| Item | Why it stays out |
|---|---|
| `.env`, `*.db`, `data/` | gitignored, and kept out of the image by the allow-list `docker/Dockerfile.dockerignore` |
| Hammerhead credentials | only in the server's env file (Ansible Vault). Never an `ARG`/`ENV`, never a build step |
| OAuth tokens | in the database on the server's volume |

`docker/check-image.sh <image>` enforces this. CI runs it before every push, and it is worth running after any local build.

## MCP server (claude.ai connector)

The MCP server is a second image, `bike-tracker-mcp`, built from
`mcp_server/` by `.github/workflows/mcp.yaml`. It is released by its own tags:

```
git tag mcp-vX.Y.Z → build → leak check → smoke test → push <user>/bike-tracker-mcp
```

A push to `main` that only touches `mcp_server/` rebuilds only this image.
App tags (`v*`) and MCP tags (`mcp-v*`) never trigger each other's workflow.

### One-time setup

- [ ] **M1. Docker Hub repository** `bike-tracker-mcp`, and the GitHub secret
  `DOCKERHUB_MCP_REPO` = `<dockerhub-user>/bike-tracker-mcp`. The user and
  token secrets are shared with the app workflow.
- [ ] **M2. Two tokens.** Generate each with `openssl rand -hex 32` and store
  both in the vault:
  - `BIKE_TRACKER_API_TOKEN`: goes into the app's `app.env` and the MCP's
    `mcp.env`, the same value in both.
  - `MCP_BEARER_TOKEN`: goes into `mcp.env` only, and is registered at the
    Cloudflare portal (M4).
- [ ] **M3. Cloudflare Zero Trust: policy first, deploy second.**
  - Create the Access policy before the tunnel goes live: exactly one e-mail
    address plus your IdP, with a hardware key and no SMS 2FA.
  - Under AI controls → MCP servers, register the server with
    `auth_type: bearer` and the `MCP_BEARER_TOKEN` value.
  - Add the portal on `mcp.hansen-doerr.de`.
  - **Tool allowlist, fail closed:** set `default_disabled: true`, then enable
    exactly these tools: `get_version`, `get_athlete_profile`,
    `list_activities`, `get_activity_summary`, `get_activity_window_stats`,
    `get_activity_streams`, `list_routes`, `get_route_history`,
    `get_fitness_history`. A tool added later stays invisible until you
    enable it.
  - Created via API or Terraform? Then add the DNS record yourself: a proxied
    CNAME to `gateway.agents.cloudflare.com`. Without it the portal answers
    `522`.
- [ ] **M4. Tunnel.** Create a tunnel whose ingress points at
  `http://bike-tracker-mcp:8000`, and put its token in
  `/opt/bike-tracker/cloudflared.env` as `TUNNEL_TOKEN=…`, mode `0600`.
- [ ] **M5. Ansible.** Add `bike-tracker-mcp` and `cloudflared` from
  `docker/compose.example.yml`. The MCP service gets **no `ports:`**.
- [ ] **M6. Verify on the server.**
  - `ss -tlnp | grep :8000` shows nothing, so there is no host binding.
  - `docker compose exec bike-tracker-mcp python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read())"`
    prints `{"status": "ok"}`.
- [ ] **M7. claude.ai.** Settings → Connectors → add
  `https://mcp.hansen-doerr.de/mcp`. The redirect URI is
  `https://claude.ai/api/mcp/auth_callback`. Then ask the coach to call
  `get_version`.
- [ ] **M8. Logs.** Read the Access logs (Zero Trust → AI controls → Logs) now
  and then.

### Every MCP release

1. Merge to `main`; the MCP workflow must go green.
2. `git tag mcp-vX.Y.Z && git push origin mcp-vX.Y.Z`
3. Rerun the playbook.

A changed API contract means **both** images: tag and deploy the app first
(`v*`), then the MCP server. `get_version` shows a mismatch as `api_version`
≠ `expected_api_version`.

## Troubleshooting

- **Pages load but nothing reacts.** The websocket can't reach the backend. Make sure the host port is `3000`.
- **`404` status on a deep link (`/rides`) in the network tab.** This is expected: with prerendering off, every route is served by the SPA fallback page, which renders correctly.
- **Container restarts in a loop.** Run `docker logs bike-tracker`. A failed migration stops the container on purpose.
- **Local build:** `docker build -f docker/Dockerfile -t bike-tracker:dev .` from the repo root; the MCP image with `docker build -f mcp_server/Dockerfile -t bike-tracker-mcp:dev mcp_server`.
- **Every MCP tool answers `status: 503`.** `BIKE_TRACKER_API_TOKEN` is missing from the app's env file, so the API fails closed.
- **Every MCP tool answers `status: 401`.** The two `BIKE_TRACKER_API_TOKEN` values differ.
- **The MCP container exits right after start.** `MCP_BEARER_TOKEN` is not set. That is deliberate, so there is never an unauthenticated endpoint.
