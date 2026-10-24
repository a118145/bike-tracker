# bike-tracker-mcp

A read-only [MCP](https://modelcontextprotocol.io) server for
[bike-tracker](https://github.com/a118145/bike-tracker), a self-hosted training
database for cycling. It gives an LLM client such as claude.ai access to rides, laps,
power, heart rate, training load, FTP history, zones, ride notes and tags.

The server is a thin pass-through. It holds no data and computes nothing. Each
tool forwards one endpoint of the bike-tracker REST API (`/api/v1`) and returns
its JSON unchanged.

```
claude.ai ─► Cloudflare MCP portal ─► Gateway + tunnel ─► cloudflared ─► bike-tracker-mcp :8000/mcp ─► bike-tracker /api/v1 ─► DB
             (OAuth, Access policy)    (private hostname)                   (bearer token)               (bearer token)
```

The server has no public hostname. The portal registers it as a private MCP
server, so it is reachable only through the tunnel. See the MCP section of
`docker/DEPLOY.md`.

## Tools

| Tool | Endpoint | What it answers |
|---|---|---|
| `get_version` | `/version` | app, API, schema and MCP versions |
| `get_athlete_profile` | `/athlete` | current FTP / LTHR / baseline, zones in W and bpm, bikes and sensors, tag vocabulary |
| `list_activities` | `/activities` | compact ride rows with filters (dates, bike, route, tag) |
| `get_activity_summary` | `/activities/{id}` | every stored metric of a ride and of each lap, note, tags, evaluations |
| `get_activity_window_stats` | `/activities/{id}/window` | every metric over part of a ride or lap (e.g. minutes 5–20) |
| `get_activity_streams` | `/activities/{id}/streams` | raw signals for a bounded window, block-averaged, never truncated |
| `list_routes` | `/routes` | derived routes, most ridden first |
| `get_route_history` | `/routes/{id}/history` | every pass over one route, plus its summary |
| `get_fitness_history` | `/fitness` | FTP/LTHR/baseline evaluations and best-power windows |

All tools are read-only. There is no write tool.

## Configuration

All configuration comes from environment variables. Nothing is baked into the image.

| Variable | Required | Meaning |
|---|---|---|
| `MCP_BEARER_TOKEN` | yes | Token the caller (the Cloudflare portal) must send as `Authorization: Bearer …`. Without it, the server refuses to start. |
| `BIKE_TRACKER_API_TOKEN` | yes | Token for the app's REST API; it must equal the app's own `BIKE_TRACKER_API_TOKEN`. |
| `BIKE_TRACKER_API_URL` | no | Default `http://bike-tracker:3000/api/v1` (the compose service name). |
| `MCP_HOST`, `MCP_PORT` | no | Default `0.0.0.0` and `8000`. |

Use two different tokens. `GET /healthz` answers `{"status": "ok"}` without a
token, for the container healthcheck. Every other path needs the bearer token.

**Never publish port 8000 on the host.** The container is reachable only
through the Docker network, by `cloudflared`.

## Run locally

```sh
cd mcp_server
uv sync
MCP_BEARER_TOKEN=dev BIKE_TRACKER_API_TOKEN=<app token> \
BIKE_TRACKER_API_URL=http://localhost:8000/api/v1 MCP_PORT=8765 \
uv run python -m bike_tracker_mcp.training_mcp
```

Always start it as a module. A file named `mcp.py` would shadow the SDK package.

## Development

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

`openapi_v1.json` is the app's API contract, generated from the app with
`uv run python -m bike_tracker.api.v1 > mcp_server/openapi_v1.json` (run from the repo
root). The tests fail when a key in it is not named in its tool's docstring.

## Releases

The tag `mcp-vX.Y.Z` publishes `X.Y.Z`, `X.Y`, `latest` and `sha-…` to Docker Hub.
The app has its own image and `vX.Y.Z` tags.
