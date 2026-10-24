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

### How it runs in production

```
claude.ai ─► Cloudflare MCP portal (mcp.hansen-doerr.de, Access login)
          ─► Gateway ─► tunnel ─► cloudflared ─► mcp.bike.internal:8000/mcp
          ─► biketracker:3000/api/v1 ─► DB
```

The MCP server has **no public hostname**. The portal reaches it as a
*private* MCP server: through Cloudflare Gateway and the tunnel, by a private
hostname route. Only the portal is on the internet, and only behind the Access
policy.

The homeserver runs plain `docker_container` tasks from the Ansible role
`bike_tracker` (`~/Hobby/IT/Ansible`), not compose.
`docker/compose.example.yml` shows the same layout as a reference.

| Container | Networks | Notes |
|---|---|---|
| `biketracker` (app) | `t2-proxy`, `bike-mcp` | Traefik on `t2-proxy`; gets `BIKE_TRACKER_API_TOKEN` |
| `bike-tracker-mcp` | `bike-mcp` only, alias `mcp.bike.internal` | **no published port, no Traefik labels**; `BIKE_TRACKER_API_URL=http://biketracker:3000/api/v1` (the container name, not the image default) |
| `cloudflared` | `bike-mcp`, `bike-egress` | pinned version, `tunnel --no-autoupdate run`; the task is skipped while the tunnel token is empty |

- `bike-mcp` is `internal: true`, so the MCP server has no internet access at
  all.
- `bike-egress` is cloudflared's way out. It is deliberately not `t2-proxy`,
  which would let cloudflared reach every service behind Traefik.
- A private hostname route needs an FQDN. cloudflared resolves it through
  Docker's DNS, so the network alias is the whole DNS setup.

Secrets live only in the Ansible vault (`inventory/group_vars/homeserver/vault.yml`)
and are named after the service: `vault_bike_tracker_api_token`,
`vault_bike_tracker_mcp_bearer_token`,
`vault_bike_tracker_cloudflared_tunnel_token`. Read one back with
`ansible-vault view`; no second copy is needed, and each can be regenerated.

### One-time setup

- [ ] **M1. Docker Hub.**
  - Create the repository `bike-tracker-mcp` (public).
  - Add three GitHub secrets for `mcp.yaml`. They are separate from the app
    workflow's:
    - `DOCKERHUB_MCP_REPO` = `<dockerhub-user>/bike-tracker-mcp`
    - `DOCKERHUB_MCP_USERNAME`
    - `DOCKERHUB_MCP_TOKEN` with scope Read, Write, Delete. Delete is needed
      for the README sync.
- [ ] **M2. Two tokens.** Generate each with `openssl rand -hex 32` straight
  into the vault:
  - `vault_bike_tracker_api_token`: goes to the app and to the MCP server,
    the same value in both.
  - `vault_bike_tracker_mcp_bearer_token`: goes to the MCP server, and into
    the portal's server entry (M6).
  - `vault_bike_tracker_cloudflared_tunnel_token`: set it to `""` for now.
    M5 fills it.
- [ ] **M3. Deploy and verify, without a tunnel yet.**
  - Run `ansible-playbook playbooks/site.yml --tags bike_tracker`.
  - From the LAN, `curl https://bike.home.hansen-doerr.de/api/v1/version`
    answers `401` without the token and the version JSON with it.
  - On the server, `ss -tlnp | grep :8000` shows nothing.
  - On the server, `docker exec bike-tracker-mcp python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read())"`
    prints `{"status":"ok"}`.
  - From inside the MCP container, a request to
    `$BIKE_TRACKER_API_URL/version` carrying `$BIKE_TRACKER_API_TOKEN` returns
    the same JSON as the LAN `curl`. That proves the network, the DNS and the
    token in one go.
- [ ] **M4. Cloudflare Zero Trust: policy first.**
  - Access controls → Policies → *Add a policy* `Arne only`, action Allow:
    - Include → Emails: exactly one address.
    - Require → Login Methods: exactly one method.
    - Protect that login with an authenticator app, not SMS.
  - Traffic policies → Traffic settings → Proxy and inspection → *Allow Secure
    Web Gateway to proxy traffic*, TCP and UDP. Private MCP servers need it.
- [ ] **M5. Tunnel with a private hostname.**
  - Networking → Tunnels → *Create a tunnel* (cloudflared).
  - Put the token from the install command into
    `vault_bike_tracker_cloudflared_tunnel_token`, and rerun the playbook.
    The tunnel turns **Healthy**.
  - Open the tunnel's **Routes** tab → *Add route* → **Private hostname** →
    `mcp.bike.internal`.
  - **No public hostname or published application.**
  - On the server: `docker logs cloudflared` shows four registered tunnel
    connections.
- [ ] **M6. Register the MCP server.** Access controls → MCP Portals →
  **MCP servers** → *Add MCP server*:

  | Field | Value |
  |---|---|
  | Server name / Server ID | `bike-tracker` (synced in the dashboard; the ID becomes the tool prefix) |
  | HTTP URL | `http://mcp.bike.internal:8000/mcp` |
  | Route traffic through Cloudflare Gateway | on (required for a private server) |
  | Authentication type | **Custom headers**: `Authorization` = `Bearer <vault_bike_tracker_mcp_bearer_token>` |
  | Access policy | `Arne only` |

  Then click *Save and connect server*. The status must turn **Ready**, with 9
  tools listed.
- [ ] **M7. Portal.**
  - Go to MCP Portals → *Add MCP server portal*.
    - Custom domain `hansen-doerr.de`, subdomain `mcp`.
    - Add server `bike-tracker`, **Require user auth** off. The server has no
      OAuth of its own; the header is its credential.
    - Policy `Arne only`.
  - Then go to Access controls → Applications → the portal → Authentication,
    and allow only the login method from M4.
  - **All tools stay enabled, with no allowlist.** A tool added in a later
    release needs no Cloudflare change.
  - The dashboard creates the DNS record. If you create the portal via API or
    Terraform, add a proxied CNAME to `gateway.agents.cloudflare.com`
    yourself. Without it the portal answers `522`.
- [ ] **M8. claude.ai.**
  - Settings → Connectors → *Add custom connector*:
    `https://mcp.hansen-doerr.de/mcp`. Leave the OAuth client fields empty;
    the portal registers the client itself.
  - Connecting runs the Access login.
  - Test: `get_version` must show `api_version` = `expected_api_version`.
  - Read the Access logs (AI controls → Logs) now and then.

**Deliberately not set:** a WAF rule that limits the portal hostname to
Anthropic's egress range (`160.79.104.0/21`). The Access login in your own
browser most likely runs through that hostname too, so check the Access logs
before you add one.

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
- **The MCP server entry in Cloudflare never turns *Ready*.**
  - A `401` means the header value is wrong. It needs the word `Bearer`, one space, then the MCP bearer token (not the API token).
  - A timeout or host not found means one of four things:
    - the private hostname route is missing;
    - the Gateway proxy (M4) is off;
    - cloudflared is not running (`docker logs cloudflared --tail 30`);
    - the MCP container lost its `mcp.bike.internal` alias.
