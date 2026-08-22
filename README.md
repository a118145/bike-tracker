# Bike Tracker

A local training-data platform for cycling: FIT files from a Hammerhead Karoo
go in, derived metrics and a Reflex web UI come out. Nothing leaves the
machine.

Design documents live in `docs/`:

| File | Contents |
|---|---|
| `masterplan.md` | UX and data model — the specification |
| `trainingsdaten-pipeline-brief.md` | Architecture, deployment, MCP layer |
| `mcp-remote-exposure-cloudflare.md` | Tunnel and portal hardening reference |
| `image.png`, `image_2.png`, `image_3.png` | The Karoo's own rendering — the spec for the stat cards, the lap bars and the map's lap markers |

## Status — milestone 1: data-first vertical slice

Ride data is real end to end and every derived number is checked against the
value the Karoo itself wrote into the FIT file. One view — **Rides** — is
built on top of it. Dashboard, Routes, Fitness and Bikes are navigable stubs.

| Area | State |
|---|---|
| FIT import, metrics, streams, route matching, bike detection | Done |
| Rides list and ride detail (map, chart, laps, stat cards) | Done |
| Full metric set per lap; ride-time chart axis; lap markers and zoom on the map | Done |
| Dashboard, Routes, Fitness, Bikes | Stubs |
| Fitness-evaluation dialog, Bikes CRUD | Not started |
| Hammerhead poller, MCP server, Docker deployment | Not started |

## Running it

```bash
uv sync
uv run reflex db migrate          # apply migrations
uv run reflex run                 # http://localhost:3000
```

First run only, to create the two bike profiles and their ANT+ sensor ids —
ride import cannot detect a bike until these exist:

```bash
uv run python -c "
import reflex as rx
from bike_tracker import models  # registers the tables
from bike_tracker.ingest.seed import seed_bikes
with rx.session() as s: seed_bikes(s)
"
```

## Importing rides

Two ways in, both ending at the same `ingest.importer.import_ride`:

- **Upload** — the *Upload FIT files* button on `/rides`.
- **Drop folder** — put `.fit` files in `data/inbox/` and press *Scan drop
  folder*. Override the location with `BIKE_TRACKER_DROP_FOLDER`.

Re-importing a file is a no-op: rides deduplicate on the SHA-256 of the file's
bytes. The files themselves are never stored — only what is extracted from
them.

## Layout

| Path | Holds |
|---|---|
| `navigation/routes.py` | Route constants. **The only place a URL string is written.** |
| `navigation/nav_config.py` | `NAV_ITEMS` + `resolve_active_route`. Drives the nav chrome. |
| `navigation/state.py` | `NavState`. The one state outside `states/`. |
| `ui/` | `base_page` template, sidebar, mobile resize gate. |
| `components/` | Cards, dialogs, toasts, map, chart, stat cards. |
| `metrics/` | Pure calculations. **Imports no Reflex and no models.** |
| `ingest/` | FIT parsing, the importer, and one module per ride source. |
| `models/` | SQLModel tables; form models where a form feeds them. |
| `pages/` | One module per page. Composes components. |
| `states/` | All other states. |
| `utils/` | Casting, formatting, validation, logging. |

### Adding a page

Four files: `navigation/routes.py`, `navigation/nav_config.py`,
`pages/your_page.py`, and `pages/__init__.py`. Missing the last one means the
`@rx.page` decorator never runs and the route silently 404s —
`tests/test_routes.py` turns that into a build failure.

## Checks

```bash
uv run pytest -q                                # 221 tests
uv run mypy .
uv run ruff check . && uv run ruff format --check .
uv run reflex compile 2>&1 | grep -i deprecat   # must print nothing
```

Before ever running `ruff format .`, check what it would walk:

```bash
uv run ruff check --show-files . | grep -Ev "^$PWD/(bike_tracker|tests)/|^$PWD/(rxconfig|pyproject)" \
  && echo "scope leaks" || echo "scope clean"
```

`[tool.ruff]` must use `extend-exclude`, never `exclude` — plain `exclude`
replaces ruff's built-in defaults, and `ruff format .` would then reformat
every package in `.venv` and still exit 0.

### The test that matters

`tests/test_fit_roundtrip.py` parses the two real Karoo files in `docs/` and
asserts every computed value against the figure the device wrote into the same
file: normalized power, intensity factor, TSS, average and maximum power,
heart rate, speed, altitude, gradient, VAM, work and power balance.

A disagreement there is a bug in `metrics/`, not a rounding difference.
Investigate it rather than widening the tolerance. Two tolerances are
deliberately loose and say why at the point they are set — TSS, where the
device uses a slightly shorter duration than the timer time in the same file,
and elevation, where the device's ascent comes from a different data source
entirely (see the module docstring of `metrics/elevation.py`).

## Database

```bash
uv run reflex db makemigrations --message "..."   # development only
uv run reflex db migrate
```

`makemigrations` never runs in a container: revisions are generated locally,
reviewed and committed. Read the generated revision before committing —
Alembic does not detect renames reliably.

Every model must be imported in `models/__init__.py`, and that package
imported at app start. Reflex only sees models the app imports, so a table
written solely by the importer would otherwise be missed by `makemigrations`
and only surface as schema drift.
