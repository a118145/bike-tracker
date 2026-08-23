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

22. **Never put text on a `Scattermap` trace.** Not a label, not a lap
    number — `mode` stays `"lines"` or `"markers"`, and `text`/`textfont`/
    `textposition` stay unset. Two independent reasons, both of which cost a
    console error and neither of which is visible from this repo:

    - Plotly gives *any* text-bearing scattermap a MapLibre symbol layer with
      `'icon-image': '{symbol}-15'` hard-coded
      (`scattermap/convert.js`), and the default `circle` symbol fills that
      token blank — so the id is the literal `-15`. No style carries it,
      MapLibre fires `styleimagemissing`, and Plotly's handler
      (`plots/map/map.js`) fetches
      `https://unpkg.com/maki@2.1.0/icons/-15.svg` with
      `crossOrigin='Anonymous'`, which unpkg does not answer. Three lines in
      the console per map load, plus an outbound dependency the self-contained
      image is not supposed to have.
    - A MapLibre style serves glyphs as PBF ranges from its own font stack, so
      naming a font it does not carry ("Arial Black") makes the label vanish
      behind a CORS error with no exception.

    Consequence: lap pins are plain coloured dots and the lap number lives in
    the hover tooltip, which is why this diverges from `docs/image_3.png` on
    purpose. `tests/test_view_models.py::test_no_map_trace_draws_text` is the
    guard — verified to fail when text is put back.

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
    `map_view.heat_opacity`, shared by the route map and the dashboard. A
    fixed alpha either makes one ride invisible or saturates ten into a solid
    blob, losing the deviations the map exists to show. Count what is actually
    *drawn*, not what exists: filtering the dashboard to one bike must not
    leave its rides as faint as when all of them were stacked.

29. **Unmount a map before reloading its page's state.** Reflex does not
    reset state on client-side navigation, so a detail page arrives holding
    the previous record's figure and renders it immediately. MapLibre then
    starts fetching the basemap while the real figure lands mid-fetch, and
    Plotly's `updateMap` only waits for the style when the style *string*
    changes — so it calls `addSource` and throws "Style is not done loading."
    The throw is unrecoverable: the rejected promise stays in `gd._promises`,
    which `Plots.previousPromises` checks first on every later plot, so the
    div never redraws again.

    **The guarantee is the React `key`, not the loading flag.** Pass a
    `record_key` that changes with the record to `map_slot`/`route_map`; it
    becomes the plot's React key, so a record change destroys and recreates
    the plot *in the same commit* and the next mount goes through `createMap`,
    which waits for the style. Key on the record only — never on a
    sub-selection like the selected lap, which must stay an in-place update
    (rule 31).

    The `loading` flag (set `True` + `yield` at the top of `load`, cleared as
    the *last* statement with no `yield` in between) is a UX affordance and a
    best-effort unmount. It is **not sufficient on its own**, for two reasons
    measured on this app:

    - it is a server round-trip, so the page has already rendered — and the
      map already mounted — with the *previous* record's figure before the
      flag can flip; and
    - when the two deltas arrive close together React commits them as one, the
      skeleton frame never renders, and nothing unmounts at all.

    `has_track` means only "this record has GPS". `stale` (`loading` or
    `loaded_id != <router id>`) is what the page body gates on, so the heading
    and stat cards do not flash the previous record's values.

    **Verify this by navigating fast.** A test that waits seconds between
    clicks separates the deltas, renders the skeleton, and passes while the
    bug is live — that is exactly how this survived a previous fix. Click at
    150–400 ms, across records with *different* trace-uid sets (different lap
    counts), and assert a new `.bt-map` element per record change. Watch the
    backend terminal too: Reflex forwards frontend exceptions there, which is
    where this was actually observed.

    Known residue: destroying a map while its style fetch is in flight makes
    MapLibre log `AbortError`. Benign, console-only, never reaches Reflex's
    error boundary, and only at sub-second navigation.

    Render the three states through `map_view.map_slot`, never an
    `rx.skeleton` wrapped around the plot: Radix keeps a skeleton's children
    mounted and only hides them with `visibility: hidden`, which would leave
    the Plotly div alive and defeat the whole mechanism.

30. **List cards use `components/cards.card_grid`.** `repeat(auto-fill,
    CARD_WIDTH)` with a *fixed* track floors the column count and keeps the
    card width; `minmax()` would stretch two cards to half the row.
    `rx.grid`'s `columns` prop cannot express this — it takes a fixed count.

31. **Every map trace carries an explicit stable `uid`, and the trace set
    never changes between re-plots of a mounted map.** Plotly matches traces
    across a re-plot by uid, and a figure that supplies none is matched *by
    position* (`plots.js::getTraceUids`): index 0 keeps index 0's identity and
    any surplus trace is given a freshly minted random uid. Selecting a lap
    therefore reads to Plotly as one trace born and one destroyed, which on a
    MapLibre subplot is `createScatterMap`→`addSource` plus
    `dispose`→`removeLayer` — measured, one such pair per lap toggle. Those
    are the two calls that throw "Style is not done loading." and "Map error."
    whenever the style is not settled, and the rejection then poisons the div
    as in rule 29. It is silent while the style happens to be loaded, which is
    exactly why it presents as intermittent.

    So: `MapTrace.uid` and `MapMarker.uid` are required, `build_map_figure`
    rejects duplicates, an undrawable trace is emitted *empty* rather than
    dropped, and `ride_map_traces` always returns both the track and the lap
    trace. `tests/test_view_models.py` pins the uid set across a lap
    selection.

    **Filtering a map means blanking traces, never removing them.**
    `dashboard_state.dashboard_traces` is the reference: every ride keeps its
    trace on every re-plot and a deselected one gets `points=[]`. Do not reach
    for `visible=False` — plotly's scattermap treats any trace whose `visible`
    is not `True` as hidden and calls `removeLayer`, which is the same
    destroy/create churn by another name.

    **The plot state vars hold a serialised `dict`, not a `go.Figure`.**
    Reflex's own figure serializer calls `plotly.io.to_json(figure)`, whose
    `remove_uids` argument defaults to `True`, so going through it deletes
    every uid on the way to the browser and silently undoes all of the above.
    `utils/plotly_json.figure_to_dict` is the one conversion point. `rx.plotly`
    declares `data` as `Var[Figure]` and enforces it, so the components re-type
    the Var with `.to(go.Figure)`; the value on the wire is unchanged.

    For the same reason, a figure handed to a mounted map always names its
    style — use `blank_map_figure`, never a bare `go.Figure()`. Plotly defaults
    a styleless figure to voyager, and a differing style *string* makes
    `updateMap` call `setStyle` and then reconcile traces on `styledata`,
    which fires before the style has finished loading.

32. **Everything FTP-dependent is rewritten by `refresh_ftp_dependent`, and
    nothing else.** Saving, editing or deleting an evaluation all call it, so
    no path can leave a ride quoting a superseded FTP. It rewrites rides from
    `valid_from` **forward** — an evaluation changes what "the last value
    before the ride" resolves to for every later ride up to the next one —
    and never touches earlier rides, which is the retroactive drift
    `ftp_at_time_w` exists to prevent (brief §5.2).

    **One fallback chain, in `resolve_fitness`, used by import and refresh
    alike:** newest evaluation at or before the ride → the head unit's own
    figure → nothing. Both callers pass through it so they cannot develop
    different ideas of the order.

    Step 2 is why `Ride.device_ftp_w` exists. The head unit's
    `threshold_power` is only in the FIT file, which is not kept (§1.3), so it
    is copied to a column at import. Without it, deleting the last evaluation
    leaves the ride quoting *that deleted evaluation's* number — the column
    still holds it, backed by nothing. Do **not** "keep whatever the column
    holds" as a fallback; that was the first attempt and it is wrong for
    exactly this case.

    There is no step 2 for LTHR: the Karoo writes no threshold heart rate.
    `max_heart_rate` is a different quantity and must never be substituted —
    it would score training load against a number nobody measured.

    `tests/test_fitness_eval.py` pins each step, and
    `tests/test_fit_roundtrip.py` pins that import stores the device figure.
    Every guard was checked by breaking the code and watching it fail.

    Only `ftp_at_time_w`, `intensity_factor` and `tss` are recomputed, from
    the stored normalized power and moving time. A full `summarize` would need
    a `ChannelWindow`, which can only be built from a parsed FIT file.

33. **`rx.slider` binds `on_value_commit`, never `on_change`.** `on_change`
    fires continuously while a thumb is dragged, so a handler that recomputes
    anything round-trips and re-serialises its figures on every mouse move —
    the cost `components/hover_cursor.py` exists to avoid. `value` takes a
    *sequence*, so a two-thumb range is one control, not two.

    Note also that `rx.segmented_control`'s `on_change` is typed
    `str | list[str]` because it supports multi-select; a single-select
    handler must accept both.

34. **A remote source deduplicates on the source's own id, before it fetches
    the bytes.** `import_ride` already refuses a duplicate by `fit_hash` — but
    only once a megabyte is on the wire. `sources/hammerhead` queries the
    `Ride.external_id` set first, so a poll over 200 known rides is one
    request instead of 201.

    The half that is easy to miss: **a ride imported by hand before the API
    was connected has no `external_id`, so it is never in that set.** When
    `import_ride` returns `created=False` because the hash matched, the
    poller stamps the activity id onto the existing ride (`_adopt`). Without
    that it is re-downloaded on every poll for ever, silently — the ride table
    looks perfect and only the traffic shows it. `source` is deliberately
    *not* rewritten: the ride really did arrive as a file.

    So the tests assert against the fake server's **request log**, not the
    ride count. A test on the ride count passes either way, which is exactly
    how this bug would survive.

35. **Credentials come from the environment; only rotating tokens are
    stored.** `HAMMERHEAD_CLIENT_ID` / `_CLIENT_SECRET` / `_REDIRECT_URI` are
    read by `ClientCredentials.from_env` and appear in no form field, no state
    var and no table. The access and refresh tokens live on
    `HammerheadAccount` because they change; the UI shows *that* they exist
    and when they expire, never their values.

    `from_env` returns `None` rather than raising, so Settings renders a
    "not configured" card instead of a traceback.

    **A rotated refresh token must be persisted the moment it is minted** —
    hence `HammerheadClient(on_token_refresh=...)`. Hammerhead may return a
    new one on every exchange; a client that forgets it works for fourteen
    hours and then fails with a message that explains nothing.

36. **`HammerheadAuthError` and `HammerheadApiError` are separate types, and
    the whole failure policy hangs off the distinction.** Auth is
    unrecoverable and switches the poller off (`enabled = False`), because
    retrying a revoked token every 15 minutes for ever is how a user never
    finds out why nothing imports. Anything else is transient and simply waits
    for the next tick. Conflating them either hammers the API or disables the
    poller because Hammerhead had a bad minute.

    Per-activity failures are recorded and the loop continues, as
    `scan_folder` already does for the drop folder — one corrupt FIT file
    costs that ride, not the other forty. The one exception is an auth failure
    *mid-download*: it aborts, because every remaining download would fail
    identically.

37. **The poll cursor only advances when the window was finished.** A run cut
    short by `MAX_DOWNLOADS_PER_RUN` leaves `cursor_date` alone, or the rides
    it did not reach are skipped permanently. And when it does advance, it
    steps back `CURSOR_OVERLAP_DAYS`: the Karoo uploads when it finds wifi, so
    an activity can appear with a `createdAt` behind the watermark. Re-listing
    a few days costs one request and rule 34's id filter discards them.

    Pin the overlap with a **written-out date**, never one computed from
    `CURSOR_OVERLAP_DAYS` — an expectation derived from the constant it pins
    passes at every value, zero included.


38. **Route matching measures distance to a polyline's *segments*, never to
    its vertices.** Measured to vertices, the smallest achievable distance is
    half the vertex spacing — so with a fixed probe count the spacing is
    `length / probes` and the effective tolerance grows with route length.
    That is the bug that produced 72 routes for 88 rides: a 6 km commute
    matched at 31 m spacing while two recordings of one 62 km loop scored
    0.365 against a true similarity of 0.987, and nothing above ~24 km could
    match at all.

    `_probe_count` is a *spacing* (`MATCH_PROBE_SPACING_M`), not a count. It
    governs how thoroughly a track is examined, and is a separate concern from
    the tolerance — do not collapse the two back together.

    The guard is `tests/test_metrics.py::test_the_match_score_does_not_depend_on_route_length`,
    which runs the same shape at 6, 60 and 200 km. Verified to fail at 60 km
    and 200 km, and pass at 6 km, when segment distance is reverted — which is
    exactly how the bug presented.

39. **Direction matters for a point-to-point ride and not for a loop.** A
    commute that climbs a hill on the way out descends it on the way back, and
    averaging the two describes neither; a loop ridden anticlockwise is the
    same route. `is_loop` (start and end within `LOOP_MARGIN_M`) decides which
    rule applies, and `opposite_directions` pairs the endpoints both ways and
    takes the closer pairing — no absolute threshold, so a ride that started a
    few streets early is unaffected.

    Both live in `metrics/geo.py` and **stay out of `route_match_score`**,
    which means geometric similarity and nothing else. The policy belongs in
    `assign_route`, next to the threshold.

    Test a loop that ends *near* but not *at* its start. A perfectly closed
    loop proves nothing: both endpoint pairings measure zero, so the guard
    passes whether or not it is there.

40. **Score only candidates that survive a cheap gate.** `route_match_score`
    costs a few hundred milliseconds and `assign_route` runs against every
    route there is. Reject on length ratio and `bbox_overlap` first — measured
    on 88 real rides, that keeps 9% of pairs and excludes no true match.
    `bbox_overlap` is relative to the *smaller* box, so a short ride inside a
    long one still reaches the real scorer.

41. **Routes are derived, so a matcher change makes them stale.**
    `ingest/backfill.py --routes` deletes every route and replays
    `assign_route` over all rides **oldest first**, so a route is established
    and named by the first time it was ridden. This cannot reuse import order:
    the Hammerhead poller works newest-first, so route names would come from
    the most recent ride of each group. Rides, laps and streams are never
    touched. It is idempotent — verify by running it twice.


## Type checking

`mypy .` is strict everywhere except `components/` and `pages/`, where several
error codes are disabled in `pyproject.toml` because mypy cannot see through
Reflex's Var system. Do not add `# type: ignore` comments there, and do not
rewrite Var expressions to satisfy a type checker that cannot read them.
