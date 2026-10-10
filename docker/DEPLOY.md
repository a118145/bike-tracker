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

## Troubleshooting

- **Pages load but nothing reacts.** The websocket can't reach the backend. Make sure the host port is `3000`.
- **`404` status on a deep link (`/rides`) in the network tab.** This is expected: with prerendering off, every route is served by the SPA fallback page, which renders correctly.
- **Container restarts in a loop.** Run `docker logs bike-tracker`. A failed migration stops the container on purpose.
- **Local build:** `docker build -f docker/Dockerfile -t bike-tracker:dev .` from the repo root.
