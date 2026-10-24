"""bike-tracker's MCP server: nine read-only tools, one per REST endpoint.

Run as a module from ``mcp_server/`` (never as a file called ``mcp.py``, which
would shadow the SDK)::

    MCP_BEARER_TOKEN=... BIKE_TRACKER_API_TOKEN=... python -m bike_tracker_mcp.training_mcp

Transport is streamable HTTP on ``:8000/mcp``. Cloudflare's Access MCP portal
sits in front and does the OAuth with claude.ai; it reaches this server with a
static bearer token (a custom ``Authorization`` header on the portal's server
entry), which :class:`BearerGuard` checks.
A blocked user can otherwise reach an upstream directly and bypass the portal's
policies (brief §3.4), which is why the token is mandatory and the server
refuses to start without it.

**Every tool is a pass-through** (AGENTS.md rules 45 and 48). The app computes;
the API shapes; this module forwards the JSON unchanged and documents it. The
docstrings are the contract with the model, so ``tests/test_contract.py``
fails when a key in ``openapi_v1.json`` is not named in its tool's docstring.
Docstrings are plain literals — an f-string is not a docstring, and FastMCP
would register the tool without a description.
"""

import hmac
import inspect
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from fastmcp import FastMCP
from starlette.types import ASGIApp, Receive, Scope, Send

from .api_client import ApiClient

MCP_VERSION = "0.1.0"
EXPECTED_API_VERSION = "v1"

MCP_TOKEN_ENV = "MCP_BEARER_TOKEN"
MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}

log = logging.getLogger("bike_tracker_mcp")

INSTRUCTIONS = """\
bike-tracker is the athlete's own training database and the single source of
truth for every training number: rides, laps, power, heart rate, training load,
FTP/LTHR history, zones, ride notes and tags. Prefer these tools over any
number written in a plan, a document or earlier in the conversation.

- Current FTP, LTHR and the zones in watts and bpm: get_athlete_profile. A
  written plan's target percentages are converted with these values, never
  with a wattage printed in the plan text.
- Units are in the key names: _w watts, _bpm beats per minute, _rpm
  revolutions per minute, _s seconds, _m metres, _ms metres per second,
  _mph metres per hour (VAM, not miles), _pct percent, _kj kilojoules,
  _kcal kilocalories, _c degrees Celsius, _kg kilograms.
- A missing value is null, never 0: no power meter, no HR strap, a ride
  too short for a 20-minute best. Do not treat null as zero.
- Rides tagged with an exclude_from_analysis tag are out of every training
  aggregate and are hidden by default; pass include_excluded=true to see them.
- Two bikes have different power meters (single-sided crank vs dual-sided
  pedals). Never mix their series in one comparison; filter by bike_id.
- Dates are the athlete's local calendar (local_date, weekday).
- Weekly volume: sum list_activities rows for that week.
- Notes are the athlete's own words; tags carry decisions. Quote notes as
  context, never as measured data.
"""

mcp = FastMCP(name="bike-tracker", instructions=INSTRUCTIONS)
_api: ApiClient | None = None


def read_only_tool(fn: Callable[..., Awaitable[dict[str, Any]]]) -> Any:
    """Register ``fn`` as a read-only tool whose description is its *whole* docstring.

    FastMCP parses Google-style docstrings and keeps only the text before the
    first section as the description — "Args:", "Returns:" and everything
    after them, "Runtime:" included, would never reach the model. The Returns
    section is the documented contract (mcp-guidelines), so the full text is
    passed explicitly; parameter descriptions are still parsed from "Args:".
    """
    return mcp.tool(fn, description=inspect.getdoc(fn), annotations=READ_ONLY)


def api() -> ApiClient:
    """The client, built from the environment on first use (tests replace it)."""
    global _api
    if _api is None:
        _api = ApiClient.from_env()
    return _api


@read_only_tool
async def get_version() -> dict[str, Any]:
    """Version of the bike-tracker app, its API and database schema, and of this
    MCP server. No units.

    When to call: when a tool returns an unexpected shape or an error that hints
    at a version mismatch, or when the user asks which version is deployed. Not
    needed before ordinary questions.

    Returns:
        dict with:
        - app_version: release of the bike-tracker app ("dev" outside a release)
        - api_version: REST API version the app serves, e.g. "v1"
        - schema_revision: database migration head, or null if unknown
        - timezone: IANA zone the app uses for local_date and weekday
        - mcp_version: version of this MCP server
        - expected_api_version: API version this MCP server was written for;
          differs from api_version only after a mismatched deploy

    Runtime: instant. Cache freely within a conversation.
    """
    result = await api().get("/version")
    return {**result, "mcp_version": MCP_VERSION, "expected_api_version": EXPECTED_API_VERSION}


@read_only_tool
async def get_athlete_profile() -> dict[str, Any]:
    """The athlete's current thresholds and training zones, bikes with their
    sensors, and the tag vocabulary. Power in W, heart rate in bpm, mass in kg,
    distance in m.

    When to call: first, whenever a question involves zones, %FTP targets,
    "is this sweetspot/threshold", sensor differences between bikes or what a
    tag means. These are the values the app's Fitness page shows. For the full
    FTP/LTHR history use get_fitness_history instead.

    Returns:
        dict with:
        - ftp_w: current FTP in W (newest FTP evaluation), null if none
        - ftp_valid_from: local date that FTP took effect
        - lthr_bpm: current lactate-threshold heart rate in bpm, null if none
        - lthr_valid_from: local date that LTHR took effect
        - baseline_w_per_bpm: current aerobic baseline (W per heartbeat at a
          fixed submaximal power), null if none
        - baseline_valid_from: local date of that baseline
        - power_zones: Coggan zones from ftp_w, each with name, description,
          low_w, high_w (high_w null = open-ended top zone); empty without FTP
        - hr_zones: Friel zones from lthr_bpm, each with name, description,
          low_bpm, high_bpm; empty without LTHR
        - bikes: each with id, name, weight_kg (system weight, rider plus
          bike), odo_m (odometer), sensors (each with sensor_type, ant_id,
          label — the label says single- or dual-sided power)
        - tags: the global vocabulary, each with id, name, color, effect
          ("exclude_from_analysis" = the ride is out of all training
          aggregates; null = descriptive only, e.g. a deviation reason)

    Runtime: instant. Re-read after the user says an FTP test was evaluated.
    """
    return await api().get("/athlete")


@read_only_tool
async def list_activities(
    from_date: str | None = None,
    to_date: str | None = None,
    bike_id: int | None = None,
    route_id: int | None = None,
    tag: str | None = None,
    include_excluded: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Compact rows of rides, newest first, with the headline numbers of each.
    Distance in m, time in s, power in W, heart rate in bpm, cadence in rpm.

    When to call: "what did I ride last week", weekly volume (sum the rows of
    a week), finding the ride for a date or a weekday slot, or the ids to
    pass to get_activity_summary. For one route's history over months use
    get_route_history; for laps and every metric of one ride use
    get_activity_summary.

    Args:
        from_date: first local date to include, YYYY-MM-DD (None = no bound)
        to_date: last local date to include, inclusive, YYYY-MM-DD
        bike_id: only this bike (ids from get_athlete_profile)
        route_id: only this route (ids from list_routes)
        tag: only rides carrying this tag name (case-insensitive)
        include_excluded: also return rides excluded from analysis
        limit: maximum rows, 1-100 (default 50)

    Returns:
        dict with the echoed filters from_date, to_date, bike_id, route_id,
        tag, include_excluded, limit; count (rows returned); and activities,
        each with:
        - id, name
        - start_time_utc; local_date, local_start_time (HH:MM), weekday
        - bike_id, bike_name, route_id, route_name
        - distance_m, moving_time_s, elevation_gain_m
        - avg_power_w, normalized_power_w, avg_hr_bpm, avg_cadence_rpm
        - intensity_factor (NP / FTP at the time), tss (training stress
          score), load_source ("power" = TSS from power, "hr" = hrTSS)
        - ftp_at_time_w: the FTP the ride was scored against
        - decoupling_pct: power-to-HR drift, first vs second half, in %
        - num_laps
        - evaluations: FTP/baseline evaluations taken from this ride
          ("FTP", "Baseline")
        - tags: tag names on the ride
        - excluded: true if out of analysis
        - note_summary: first line of the athlete's note plus sleep / RPE /
          breathing quick fields, null if no note
        On a bad filter: error (and known_tags for an unknown tag), status.

    Runtime: fast. Re-query after new rides may have been imported.
    """
    return await api().get(
        "/activities",
        from_date=from_date,
        to_date=to_date,
        bike_id=bike_id,
        route_id=route_id,
        tag=tag,
        include_excluded=include_excluded,
        limit=limit,
    )


@read_only_tool
async def get_activity_summary(activity_id: int) -> dict[str, Any]:
    """Everything stored about one ride: every metric, every lap with the same
    metrics, the athlete's note, tags and evaluations. Units are in the key
    names (_w W, _bpm bpm, _rpm rpm, _s s, _m m, _ms m/s, _mph metres per
    hour, _pct %, _kj kJ, _kcal kcal, _c °C).

    When to call: to judge one session — interval compliance per lap (the
    athlete presses the lap button per interval), the climb cap on an easy
    day, decoupling, cadence, or why a ride deviated (note, tags). Use
    get_activity_window_stats for a part of a lap (e.g. minutes 5-20), and
    get_activity_streams only to look at the raw signal.

    Args:
        activity_id: ride id from list_activities

    Returns:
        dict with the ride's identity:
        - id, name, sport, num_laps
        - start_time_utc; local_date, local_start_time, weekday
        - bike_id, bike_name, route_id, route_name
        - tags, excluded
        - note: null, or text, sleep_quality (1-5), rpe (1-10, Borg CR10),
          perceived_breathing_limit (1-5)
        - evaluations: each with id, type ("ftp", "lthr", "baseline"),
          value, unit, valid_from_utc, local_date, ride_id, method
          ("window", "lap", "best_fit"), source ("ride" or "manual")
        - stream_channels: channels get_activity_streams can return
        plus the metric keys, the same for the ride, each lap and a window:
        - time: elapsed_time_s, moving_time_s, paused_time_s
        - distance and climbing: distance_m, elevation_gain_m,
          elevation_loss_m, avg_altitude_m, max_altitude_m, min_altitude_m,
          avg_grade_pct, max_grade_pct, min_grade_pct, avg_vam_mph
        - speed: avg_speed_ms, max_speed_ms, min_speed_ms
        - heart rate: avg_hr_bpm, max_hr_bpm, min_hr_bpm
        - cadence: avg_cadence_rpm (pedalling only), max_cadence_rpm,
          min_cadence_rpm
        - power: avg_power_w, max_power_w, min_power_w, normalized_power_w,
          energy_kj, calories_kcal, left_balance_pct (dual-sided meter only)
        - best average power over a window: best_power_5s_w,
          best_power_15s_w, best_power_30s_w, best_power_60s_w,
          best_power_300s_w, best_power_600s_w, best_power_1200s_w,
          best_power_3600s_w (null if shorter than the window)
        - load: power_available, load_source, ftp_at_time_w,
          lthr_at_time_bpm, intensity_factor, tss, decoupling_pct
        - temperature: avg_temperature_c, max_temperature_c,
          min_temperature_c
        - laps: each with the metric keys plus index (0-based; the app
          labels laps from 1), start_time_utc, local_start_time (HH:MM:SS),
          window_start_s and window_end_s (the lap on the stream clock, for
          get_activity_window_stats)
        On an unknown id: error, status 404.

    Runtime: fast (stored values). Cache per activity within a conversation.
    """
    return await api().get(f"/activities/{activity_id}")


@read_only_tool
async def get_activity_window_stats(
    activity_id: int,
    lap_index: int | None = None,
    start_s: float | None = None,
    end_s: float | None = None,
    skip_head_s: float = 0.0,
    skip_tail_s: float = 0.0,
) -> dict[str, Any]:
    """Every metric over part of a ride, computed by the app's own summary
    function from the stored streams — the same numbers a lap has, for any
    window. Times in s on the stream clock (seconds since the first sample),
    power in W, heart rate in bpm.

    When to call: protocols that read a sub-window, e.g. "average HR of
    minutes 5-20 of the 20-minute test" (lap_index with skip_head_s=300),
    "HR at the end of the block" (start_s/end_s over the last 60 s of a lap,
    from window_start_s/window_end_s in get_activity_summary), the warm-up
    check, or a segment without a lap. For a whole lap the stored lap in
    get_activity_summary is already there — do not recompute it here.

    Args:
        activity_id: ride id
        lap_index: cut this lap (0-based); or give start_s and end_s instead
        start_s: window start in s on the stream clock (with end_s)
        end_s: window end in s on the stream clock
        skip_head_s: seconds dropped from the start of the window
        skip_tail_s: seconds dropped from the end of the window

    Returns:
        dict with activity_id, lap_index (echo), start_s and end_s (first and
        last sample actually used), sample_count, ftp_estimate_w (0.95 x the
        window's average power — meaningful only over a 20-minute all-out
        effort), baseline_w_per_bpm (average power / average HR), plus the
        metric keys as in get_activity_summary:
        elapsed_time_s, moving_time_s, paused_time_s, distance_m,
        elevation_gain_m, elevation_loss_m, avg_altitude_m, max_altitude_m,
        min_altitude_m, avg_grade_pct, max_grade_pct, min_grade_pct,
        avg_vam_mph, avg_speed_ms, max_speed_ms, min_speed_ms, avg_hr_bpm,
        max_hr_bpm, min_hr_bpm, avg_cadence_rpm, max_cadence_rpm,
        min_cadence_rpm, avg_power_w, max_power_w, min_power_w,
        normalized_power_w, energy_kj, calories_kcal, left_balance_pct,
        best_power_5s_w, best_power_15s_w, best_power_30s_w, best_power_60s_w,
        best_power_300s_w, best_power_600s_w, best_power_1200s_w,
        best_power_3600s_w, power_available, load_source, ftp_at_time_w,
        lthr_at_time_bpm, intensity_factor, tss, decoupling_pct,
        avg_temperature_c, max_temperature_c, min_temperature_c.
        Distance, climbing and speed are computed from the streams here and
        can differ by a few percent from the device's own lap figures.
        On a bad window: error, status 422 (lap_indices lists valid laps).

    Runtime: one ride's streams are decoded, well under a second. Reuse the
    result for the same window.
    """
    return await api().get(
        f"/activities/{activity_id}/window",
        lap_index=lap_index,
        start_s=start_s,
        end_s=end_s,
        skip_head_s=skip_head_s,
        skip_tail_s=skip_tail_s,
    )


@read_only_tool
async def get_activity_streams(
    activity_id: int,
    start_s: float,
    end_s: float,
    step_s: int = 5,
    channels: str = "power_w,hr_bpm,cadence_rpm",
) -> dict[str, Any]:
    """Raw per-sample signals for a bounded window of one ride, averaged into
    blocks of step_s seconds. Times in s on the stream clock, channels in the
    units of their names.

    When to call: only to look at the shape of an effort (pacing inside an
    interval, HR lag, a dropout) after get_activity_summary and
    get_activity_window_stats could not answer. Never to recompute averages —
    those tools already have them.

    Args:
        activity_id: ride id
        start_s: window start in s on the stream clock
        end_s: window end in s; the window may span at most 3600 s
        step_s: block length in s, 1-300 (default 5)
        channels: comma-separated, from altitude_m, speed_ms, hr_bpm,
            cadence_rpm, power_w, temperature_c (get_activity_summary lists
            which the ride recorded in stream_channels)

    Returns:
        dict with activity_id, start_s, end_s, step_s (echo), sample_count
        (blocks), time_s (block start times), channels (name -> list of block
        means, null where no sample; an empty list for a channel the ride did
        not record). If the answer would exceed the response budget: error,
        status 422, max_window_s_at_this_step and suggested_step_s — retry
        with a shorter window or the suggested step. Never truncated.

    Runtime: decodes one ride's streams, under a second.
    """
    return await api().get(
        f"/activities/{activity_id}/streams",
        start_s=start_s,
        end_s=end_s,
        step_s=step_s,
        channels=channels,
    )


@read_only_tool
async def list_routes() -> dict[str, Any]:
    """Every route the app derived from repeated rides, most ridden first.
    Distance in m, climbing in m, time in s.

    When to call: to find the id of a regular course (the commute out or
    back, a test climb loop) before get_route_history, or to answer "which
    routes do I ride most". Route names are what the athlete called them;
    out and back of a commute are separate routes.

    Returns:
        dict with count and routes, each with:
        - id, name
        - ride_count: rides in analysis on this route
        - avg_distance_m, avg_elevation_gain_m, avg_moving_time_s: averages
          over those rides, null if none counts
        - last_ridden_local_date: local date of the newest ride in analysis

    Runtime: instant.
    """
    return await api().get("/routes")


@read_only_tool
async def get_route_history(route_id: int, include_excluded: bool = True) -> dict[str, Any]:
    """Every pass over one route, newest first, with the numbers that compare
    passes, plus the route's summary. Time in s, speed in m/s, power in W,
    heart rate in bpm.

    When to call: trends on a fixed course — "is my HR on the commute climb
    dropping", "fastest time on the test loop", power at the same route over
    weeks. Compare only passes on the same bike_id (different power meters)
    and read note_summary/tags before calling a pass an outlier.

    Args:
        route_id: id from list_routes
        include_excluded: also list passes excluded from analysis (flagged);
            the summary figures never count them

    Returns:
        dict with:
        - route: id, name, ride_count, avg_distance_m, avg_elevation_gain_m,
          avg_moving_time_s, last_ridden_local_date
        - include_excluded (echo), rides_in_analysis, rides_excluded
        - fastest_moving_time_s, fastest_on_local_date, avg_tss, best_tss:
          over the rides in analysis
        - passes: each with activity_id, name, start_time_utc, local_date,
          local_start_time, weekday, bike_id, bike_name, moving_time_s,
          avg_speed_ms, avg_power_w, normalized_power_w, best_power_1200s_w,
          intensity_factor, tss, avg_hr_bpm, tags, excluded, note_summary
        On an unknown id: error, status 404.

    Runtime: fast (stored values).
    """
    return await api().get(f"/routes/{route_id}/history", include_excluded=include_excluded)


@read_only_tool
async def get_fitness_history() -> dict[str, Any]:
    """FTP, LTHR and baseline evaluations over time, and the best average
    power per duration in consecutive 30- and 90-day windows. Power in W,
    heart rate in bpm, baseline in W per bpm.

    When to call: FTP progression against a plan's expectation corridor,
    "when was my last FTP test", whether short or long power is improving.
    For the current FTP and zones alone, get_athlete_profile is enough.

    Returns:
        dict with:
        - evaluations: newest first, each with id, type ("ftp", "lthr",
          "baseline"), value, unit ("W", "bpm", "W/bpm"), valid_from_utc,
          local_date, ride_id (null if entered by hand), method, source
          ("ride" or "manual")
        - power_curves: windows counted back from today, newest first per
          length, each with window_days (30 or 90), label, start_local_date,
          end_local_date, ride_count, and the best power in that window:
          best_power_5s_w, best_power_15s_w, best_power_30s_w,
          best_power_60s_w, best_power_300s_w, best_power_600s_w,
          best_power_1200s_w, best_power_3600s_w (null = no ride long enough)

    Runtime: fast (stored values).
    """
    return await api().get("/fitness")


class BearerGuard:
    """Refuse every HTTP request without the portal's bearer token.

    Pure ASGI, so the MCP app's lifespan passes straight through. The token is
    compared in constant time. ``/healthz`` stays open for the container
    healthcheck and returns nothing but "ok".
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == HEALTH_PATH:
            await _respond(send, 200, {"status": "ok"})
            return

        headers = dict(scope.get("headers", []))
        scheme, _, token = headers.get(b"authorization", b"").decode("latin-1").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            token.strip().encode(), self._token
        ):
            await _respond(
                send, 401, {"error": "Missing or invalid bearer token."}, bearer_challenge=True
            )
            return
        await self.app(scope, receive, send)


async def _respond(
    send: Send, status: int, body: dict[str, str], *, bearer_challenge: bool = False
) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    if bearer_challenge:
        headers.append((b"www-authenticate", b"Bearer"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def create_app(token: str) -> ASGIApp:
    """The MCP endpoint behind the bearer check. Stateless: one container,
    read-only tools, and nothing gained from session affinity behind a proxy."""
    return BearerGuard(mcp.http_app(path=MCP_PATH, stateless_http=True), token)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    token = os.environ.get(MCP_TOKEN_ENV, "").strip()
    if not token:
        log.error(
            "%s is not set; refusing to serve an unauthenticated MCP endpoint.", MCP_TOKEN_ENV
        )
        sys.exit(1)
    uvicorn.run(
        create_app(token),
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8000")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
