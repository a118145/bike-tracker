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

20. **`metrics.geo.TILE_SIZE_PX` is 512, not 256.** MapLibre — which Plotly's
    `map` subplot renders with — defines zoom against 512 px tiles. The
    Leaflet/Google 256 makes every zoom one level too high.
    `tests/test_metrics.py` inverts the framing maths and asserts the bounding
    box fits, so this cannot regress silently.

21. **`bounds_to_center_zoom`'s `width_px` under-states the container on
    purpose.** The result is the smaller of the two axis fits, so a narrow
    guess only ever letterboxes; a wide one clips.

22. **Never name a font in a `Scattermap` `textfont`.** A MapLibre style
    serves glyphs from its own font stack; naming one it does not carry
    ("Arial Black") makes the label vanish behind a CORS error with no
    exception.

23. **The chart-to-map hover cursor is the one piece of custom JS.** It is
    client-side by necessity — see `components/hover_cursor.py`. If you add
    another plotly-touching script, import `plotly.js/dist/plotly`, never
    `plotly.js`: the package root is CommonJS and its Vite interop shim
    references `global`, which blanks the page.

24. **Read ORM attributes before the session commits.** A commit expires every
    instance, so touching one after the `with rx.session()` block raises
    `DetachedInstanceError`. Capture ids and names into locals first — this
    bit `BikesState.submit_form`, where the sensor rewrite commits a second
    time.

25. **A route with no rides is deleted, and the pruning lives in
    `refresh_route_stats`.** That is the one choke point every count change
    passes through — it runs on both sides of a reassignment — so a hook
    anywhere else is bypassable. It returns `Route | None`; never call it
    before the ride's new `route_id` is committed.

26. **A card carrying an inline action must not be an `rx.link`.**
    `stop_propagation` halts bubbling but does not cancel an anchor's
    activation, so the click navigates anyway. Use `on_click` plus
    `rx.redirect` on the card, as `pages/routes.py` and `pages/bikes.py` do.

27. **Geometry for maps is precomputed, never derived per request.**
    `Ride.map_points` exists because rebuilding a polyline from `RideStream`
    costs ~20 ms a ride — seconds on a route with a hundred of them. Storing
    it is free: `import_ride` already has the decoded track in hand.

28. **Heat-map opacity is a function of the ride count, not a constant.** See
    `route_detail_state.heat_opacity`. A fixed alpha either makes one ride
    invisible or saturates ten into a solid blob, losing the deviations the
    map exists to show.

29. **Unmount a map before reloading its page's state.** Reflex does not
    reset state on client-side navigation, so a detail page arrives holding
    the previous record's figure and renders it immediately. MapLibre then
    starts fetching the basemap while the real figure lands mid-fetch, and
    Plotly's `updateMap` only waits for the style when the style *string*
    changes — so it calls `addSource` and throws "Style is not done loading."
    The throw is unrecoverable: the rejected promise stays in `gd._promises`,
    which `Plots.previousPromises` checks first on every later plot, so the
    div never redraws again. Set `has_track = False` and `yield` at the top of
    `load` — that unmounts the plot, runs `Plotly.purge`, and guarantees the
    next mount coincides with the final figure.

30. **List cards use `components/cards.card_grid`.** `repeat(auto-fill,
    CARD_WIDTH)` with a *fixed* track floors the column count and keeps the
    card width; `minmax()` would stretch two cards to half the row.
    `rx.grid`'s `columns` prop cannot express this — it takes a fixed count.

## Type checking

`mypy .` is strict everywhere except `components/` and `pages/`, where several
error codes are disabled in `pyproject.toml` because mypy cannot see through
Reflex's Var system. Do not add `# type: ignore` comments there, and do not
rewrite Var expressions to satisfy a type checker that cannot read them.
