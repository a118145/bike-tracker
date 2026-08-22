<!-- reflex managed begin (do not edit inside this block; add custom content outside the markers) -->
# Agents

This project uses the **Reflex agent skills** — up-to-date knowledge for building full-stack Python web apps with [Reflex](https://reflex.dev). Install them first, then use them as described below.

## Installing the Skills

Before working on this project, make sure the Reflex skills are available to you.

### Claude Code

If the skills are not already available, install them yourself by running these shell commands:

```bash
claude plugin marketplace add reflex-dev/agent-skills
claude plugin install reflex@reflex-agent-skills
```

Newly installed plugins load on the next session. After installing, tell the user to restart the session so the skills auto-load; until then, read the skill files directly from `~/.claude/plugins/` when you need them.

### Other agents (Cursor, OpenCode, Codex, Pi)

```
npx skills add reflex-dev/agent-skills
```

Or clone https://github.com/reflex-dev/agent-skills and copy the `skills/` folders into your agent's skill directory (see the repo README for paths).

### Verifying

Before writing or editing any Reflex code, confirm these three skills are available: `reflex-docs`, `setup-python-env`, and `reflex-process-management`. If they are not, STOP and run the install step above — do not proceed without them.

## Using the Skills

### Reflex documentation

For anything about Reflex APIs — components, state management, events, styling, database, routing, authentication — use the **reflex-docs** skill rather than relying on memory. It carries current, version-accurate docs.

### Initializing a new Reflex project

When starting a new Reflex project or setting up a development environment, you **must** follow the **setup-python-env** skill before doing anything else.

Do not skip any steps. Do not assume a virtual environment or Reflex is already available — always verify first by following the skill's instructions in order.

After the environment is ready and Reflex is installed, run:

```bash
reflex init
```

Then proceed with the user's request.

### Managing a Reflex process

When you need to compile, run, reload, or debug a Reflex application, follow the **reflex-process-management** skill for the correct sequence and error investigation steps.
<!-- reflex managed end -->

---

# Project conventions

Everything below is project-specific and takes precedence over generic Reflex
advice. Read it before editing.

## Folder contract

| Path | Holds |
|---|---|
| `navigation/routes.py` | Route constants. **The only place a URL string is written.** |
| `navigation/nav_config.py` | `NAV_ITEMS` + `resolve_active_route`. Drives the nav chrome. |
| `navigation/state.py` | `NavState`. The one state outside `states/`. |
| `ui/` | `base_page` template, sidebar, mobile resize gate. |
| `components/` | Dialogs, toasts, cards, map, chart, stat cards. Reused by pages. |
| `metrics/` | Pure calculations. Imports no Reflex and no models. |
| `ingest/` | FIT parsing, the importer, one module per ride source. |
| `models/` | SQLModel tables; form models only where a form feeds them. |
| `pages/` | One module per page. Composes components. |
| `states/` | All other states. |
| `utils/` | Casting, formatting, validation, logging. |

## Rules

1. **Navigation highlighting is derived, never stored.** `NavState` has only
   computed vars reading `self.router.url.path`. Nav links are plain `href=`.
   Never add an `on_click` to "keep nav state in sync" — click-tracking
   desyncs on deep links, F5 and browser back/forward.
   `tests/test_navigation.py` guards this.

2. **States are flat.** Subclass `rx.State` directly. Share via
   `await self.get_state(Other)`, not inheritance.

3. **A new page touches four files**: `routes.py`, `nav_config.py`,
   `pages/your_page.py`, `pages/__init__.py`. Missing the last one means the
   `@rx.page` decorator never runs and the route silently 404s.

4. **`components/__init__.py` stays empty of re-exports.** Components import
   from `states/`; states import toast helpers from `components/`. Eager
   re-exports make that circular. Likewise `ui/` must never import from
   `pages/` — that is why the resize gate lives in `ui/`.

5. **Log events, not sentences**: `log.info("ride.import", ride_id=7)`.

6. **Form data is cast before validation** via `utils/validation.parse_form`,
   which returns `(instance | None, errors)`. On errors, return early from the
   handler so the dialog stays open with the user's input intact.

7. **Never mount a second toaster.** `rx.App` provides one.

## Rules specific to this app

8. **`metrics/` imports no Reflex and no models.** Arrays and dataclasses in,
   scalars out. This is the condition that lets the metric tests run in
   milliseconds without an app, a database or fixtures — which is what makes
   it practical to check every number against a real FIT file.

9. **Every model is imported in `models/__init__.py`, and that package is
   imported at app start.** Reflex only sees models the app imports; a table
   written solely by the importer is otherwise missed by `makemigrations`.

10. **Every ride source calls `ingest.importer.import_ride`.** Sources bring
    bytes; none of them may reimplement any part of the import sequence.

11. **Precompute, do not recompute per query.** Ride metrics, route averages
    and list-card thumbnail geometry are all written at import time. A page
    load must not decompress GPS streams.

12. **Unit suffixes on field names** (`distance_m`, `avg_power_w`,
    `ftp_at_time_w`) so the REST and MCP layers stay pure pass-throughs. A
    rename at that boundary is where translation bugs move in.

13. **Missing data stays missing.** A ride with no power meter, no GPS fix or
    no cadence sensor is normal. Those render as an em dash, never as a zero,
    and NaN — not 0.0 — is how an absent sample is spelled in a stream.

14. **Never `@rx.event(background=True)` for the poller.** Such tasks live
    only as long as a browser session. Use APScheduler on the backend
    lifecycle (brief §3.2).

15. **The device is the reference.** When adding a metric, add its check to
    `tests/test_fit_roundtrip.py` against the FIT session field. If they
    disagree, that is a bug — investigate rather than widening the tolerance.
    The two loose tolerances that exist are justified where they are set.

16. **Metric columns go on `models/metric_fields.py`, never on `Ride` alone.**
    `Ride` and `Lap` both inherit `MetricFields`, which is what lets a lap
    fill the same stat cards a ride does. `tests/test_view_models.py` pins the
    difference between the two field sets to an explicit list, so a metric
    added to `Ride` directly is a build failure.

17. **A lap is summarized over its own slice of the streams.** One function,
    `ingest/summarize.py::summarize`, serves rides and laps alike. Never fall
    back to a whole-ride value for a lap: it looks like lap data and is wrong.
    Note two FIT keys are session-only (`avg_temperature`, `max_temperature`)
    and `normalized_power` is missing from some lap messages, so those are
    always computed.

18. **`cross_check` runs for the ride, never per lap.** Lap ascents are small
    integers, so relative gaps explode and would fail on correct data.

19. **`layout.uirevision` on any map figure is load-bearing.** Plotly keeps
    `map.center`/`zoom` across a re-plot while it is unchanged — and `None`
    counts as unchanged, which is how a new ride inherits the previous ride's
    camera. It must vary with whatever should move the viewport.

## Type checking

`mypy .` is strict everywhere except `components/` and `pages/`, where several
error codes are disabled in `pyproject.toml` because mypy cannot see through
Reflex's Var system. Do not add `# type: ignore` comments there, and do not
rewrite Var expressions to satisfy a type checker that cannot read them.
