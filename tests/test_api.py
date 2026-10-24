"""The REST API serves what the app shows (AGENTS.md rules 45-47).

Most tests here are **parity** tests: each endpoint is compared with the
stored row or the shared helper the page renders from, never with a number
worked out again in the test. A test that recomputed a metric would pass with
the API and the page disagreeing, as long as both disagreed the same way.

The database is the two reference FIT files, imported once per module through
the real importer.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import reflex as rx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, col, create_engine, select

from bike_tracker import metrics
from bike_tracker.api import schemas
from bike_tracker.api.auth import TOKEN_ENV
from bike_tracker.api.v1 import create_api
from bike_tracker.ingest.seed import seed_bikes
from bike_tracker.ingest.sources.file import import_file
from bike_tracker.ingest.summarize import ELEVATION_TOLERANCE, METRIC_TOLERANCE
from bike_tracker.models import (
    EVAL_FTP,
    EVAL_LTHR,
    TAG_EFFECT_EXCLUDE,
    FitnessEval,
    Lap,
    MetricFields,
    Ride,
    RideTag,
    Tag,
    lap_window_s,
    latest_evaluations,
    route_summary,
)
from bike_tracker.utils.local_time import to_local

from .conftest import EVENING_FIT, MORNING_FIT

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
OPENAPI_SNAPSHOT = Path(__file__).resolve().parent.parent / "mcp_server" / "openapi_v1.json"


@pytest.fixture(scope="module")
def db_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Both reference rides, two evaluations, and an excluding tag (unused yet)."""
    url = f"sqlite:///{tmp_path_factory.mktemp('api') / 'api.db'}"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_bikes(session)
        import_file(EVENING_FIT, session)
        import_file(MORNING_FIT, session)
        rides = session.exec(select(Ride).order_by(col(Ride.start_time))).all()
        morning, evening = rides[0], rides[1]
        for when, value, kind in (
            (morning.start_time, 230.0, EVAL_FTP),
            (evening.start_time, 245.0, EVAL_FTP),
            (evening.start_time, 176.0, EVAL_LTHR),
        ):
            session.add(
                FitnessEval(
                    type=kind,
                    value=value,
                    valid_from=when,
                    start_time=when,
                    end_time=when,
                    ride_id=None if kind == EVAL_LTHR else morning.id,
                )
            )
        session.add(Tag(name="Not training", effect=TAG_EFFECT_EXCLUDE))
        session.commit()
    yield url
    engine.dispose()


@pytest.fixture(scope="module")
def client(db_url: str) -> Iterator[TestClient]:
    with pytest.MonkeyPatch.context() as patch:
        original = rx.session
        patch.setattr(rx, "session", lambda *a, **kw: original(url=db_url))
        patch.setenv(TOKEN_ENV, TOKEN)
        patch.setenv("APP_TIMEZONE", "Europe/Berlin")
        yield TestClient(create_api())


@pytest.fixture
def session(db_url: str) -> Iterator[Session]:
    engine = create_engine(db_url)
    with Session(engine) as open_session:
        yield open_session
    engine.dispose()


def _get(client: TestClient, path: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/v1{path}", params=params, headers=AUTH)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _evening(session: Session) -> Ride:
    ride = session.exec(select(Ride).where(Ride.num_laps > 1)).first()
    assert ride is not None
    return ride


# ---------------------------------------------------------------------------
# Auth: fail closed
# ---------------------------------------------------------------------------


def test_no_configured_token_disables_the_api(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_ENV)
    response = client.get("/api/v1/version", headers=AUTH)
    assert response.status_code == 503
    assert TOKEN_ENV in response.json()["detail"]["error"]


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}]
)
def test_a_missing_or_wrong_token_is_refused(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/api/v1/activities", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_the_openapi_document_is_not_served(client: TestClient) -> None:
    """It would be readable without the token on the GUI's host."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


# ---------------------------------------------------------------------------
# Parity with the pages
# ---------------------------------------------------------------------------


def test_version_reports_the_api_version_and_timezone(client: TestClient) -> None:
    body = _get(client, "/version")
    assert body["api_version"] == "v1"
    assert body["timezone"] == "Europe/Berlin"
    assert body["app_version"] == "dev"


def test_athlete_is_the_fitness_page_header_and_zone_tables(
    client: TestClient, session: Session
) -> None:
    body = _get(client, "/athlete")
    latest = latest_evaluations(session.exec(select(FitnessEval)).all())

    assert body["ftp_w"] == latest[EVAL_FTP].value == 245.0, "newest by valid_from wins"
    assert body["lthr_bpm"] == latest[EVAL_LTHR].value
    assert body["baseline_w_per_bpm"] is None, "no baseline evaluation, so none (rule 13)"
    assert [(z["low_w"], z["high_w"]) for z in body["power_zones"]] == [
        (z.low, z.high) for z in metrics.power_zones(245.0)
    ]
    assert [(z["low_bpm"], z["high_bpm"]) for z in body["hr_zones"]] == [
        (z.low, z.high) for z in metrics.hr_zones(176.0)
    ]
    assert {bike["name"] for bike in body["bikes"]} >= {"Canyon CF SL 7", "Ideal Trekking"}
    assert body["tags"] == [
        {"id": 1, "name": "Not training", "color": "gray", "effect": TAG_EFFECT_EXCLUDE}
    ]


def test_activity_rows_carry_the_stored_values_newest_first(
    client: TestClient, session: Session
) -> None:
    body = _get(client, "/activities")
    rows = body["activities"]
    stored = session.exec(select(Ride).order_by(col(Ride.start_time).desc())).all()

    assert [row["id"] for row in rows] == [ride.id for ride in stored]
    for row, ride in zip(rows, stored, strict=True):
        local = to_local(ride.start_time)
        assert row["local_date"] == local.date().isoformat()
        assert row["local_start_time"] == local.strftime("%H:%M")
        for key in ("distance_m", "moving_time_s", "normalized_power_w", "tss", "decoupling_pct"):
            assert row[key] == getattr(ride, key), key
    assert rows[1]["evaluations"] == ["FTP", "FTP"], "the rides list's badges"
    assert rows[0]["weekday"] == "Wednesday"


def test_the_date_filter_uses_the_local_calendar(client: TestClient) -> None:
    body = _get(client, "/activities", from_date="2026-08-19", to_date="2026-08-19")
    assert [row["name"] for row in body["activities"]] == ["Evening Ride"]
    assert body["from_date"] == "2026-08-19", "the filter is echoed"


def test_an_excluded_ride_is_out_by_default_and_flagged_when_asked_for(
    client: TestClient, session: Session
) -> None:
    evening = _evening(session)
    tag = session.exec(select(Tag)).one()
    session.add(RideTag(ride_id=evening.id or 0, tag_id=tag.id or 0))
    session.commit()
    try:
        default = _get(client, "/activities")["activities"]
        assert evening.id not in [row["id"] for row in default]

        everything = _get(client, "/activities", include_excluded=True)["activities"]
        flagged = next(row for row in everything if row["id"] == evening.id)
        assert flagged["excluded"] is True
        assert flagged["tags"] == ["Not training"]

        tagged = _get(client, "/activities", tag="not training", include_excluded=True)
        assert [row["id"] for row in tagged["activities"]] == [evening.id]
    finally:
        link = session.get(RideTag, (evening.id, tag.id))
        session.delete(link)
        session.commit()


def test_an_unknown_tag_lists_the_known_ones(client: TestClient) -> None:
    response = client.get("/api/v1/activities", params={"tag": "nope"}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["detail"]["known_tags"] == ["Not training"]


def test_activity_detail_is_the_ride_and_its_lap_rows(client: TestClient, session: Session) -> None:
    ride = _evening(session)
    laps = session.exec(select(Lap).where(Lap.ride_id == ride.id).order_by(col(Lap.index))).all()
    body = _get(client, f"/activities/{ride.id}")

    for name in MetricFields.model_fields:
        assert body[name] == getattr(ride, name), name
    assert [lap["index"] for lap in body["laps"]] == [lap.index for lap in laps]
    for out, lap in zip(body["laps"], laps, strict=True):
        for name in MetricFields.model_fields:
            assert out[name] == getattr(lap, name), (lap.index, name)
        assert (out["window_start_s"], out["window_end_s"]) == lap_window_s(ride, lap)
    assert body["note"] is None
    assert set(body["stream_channels"]) >= {"power_w", "hr_bpm", "altitude_m"}


def test_a_missing_activity_is_a_404(client: TestClient) -> None:
    response = client.get("/api/v1/activities/9999", headers=AUTH)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# The window cutter is summarize()
# ---------------------------------------------------------------------------

# Values import copies from the FIT lap message rather than computing them
# (masterplan §3.3). With no message for an arbitrary window the cutter
# computes them from the streams, so for a lap they agree only within the
# tolerances import's own cross-check uses (ingest/summarize.py) — and the
# timer to the 5 s the moving-time axis is held to in test_fit_roundtrip.py.
# TSS scales with that timer, which is why it is in here: 3 s on a 13-minute
# lap is 0.4 %.
DEVICE_COPIED_REL = {
    "distance_m": METRIC_TOLERANCE,
    "avg_speed_ms": METRIC_TOLERANCE,
    "avg_cadence_rpm": METRIC_TOLERANCE,
    "avg_hr_bpm": METRIC_TOLERANCE,
    "avg_power_w": METRIC_TOLERANCE,
    "avg_altitude_m": METRIC_TOLERANCE,
    "avg_vam_mph": METRIC_TOLERANCE,
    "tss": METRIC_TOLERANCE,
    "elevation_gain_m": ELEVATION_TOLERANCE,
    "elevation_loss_m": ELEVATION_TOLERANCE,
}
DEVICE_TIMER_ABS_S = {"elapsed_time_s": 5.0, "moving_time_s": 5.0, "paused_time_s": 5.0}
# Streams are stored as float32 (ingest/streams.py); these come out of the
# same arithmetic and differ from the import-time float64 run in the 7th digit.
FLOAT32_ROUND_TRIP = {
    "max_speed_ms",
    "min_speed_ms",
    "max_altitude_m",
    "min_altitude_m",
    "avg_grade_pct",
    "max_grade_pct",
    "min_grade_pct",
}


@pytest.mark.parametrize(
    ("laps", "lap_index"),
    [(4, 0), (4, 1), (4, 2), (4, 3), (1, 0)],
    ids=["evening-1", "evening-2", "evening-3", "evening-4", "morning-with-29-min-stop"],
)
def test_a_lap_window_reproduces_the_stored_lap(
    client: TestClient, session: Session, laps: int, lap_index: int
) -> None:
    """Every stream-computed field exactly; device-copied ones within tolerance.

    The morning lap is the whole ride including a 29-minute stop. Before the
    cutter derived riding time from the stream, it scored that stop as riding:
    TSS 229 against the stored 176.
    """
    ride = session.exec(select(Ride).where(Ride.num_laps == laps)).one()
    lap = session.exec(select(Lap).where(Lap.ride_id == ride.id, Lap.index == lap_index)).one()
    body = _get(client, f"/activities/{ride.id}/window", lap_index=lap_index)

    for name in MetricFields.model_fields:
        stored, cut = getattr(lap, name), body[name]
        if name in DEVICE_COPIED_REL:
            assert cut == pytest.approx(stored, rel=DEVICE_COPIED_REL[name]), name
        elif name in DEVICE_TIMER_ABS_S:
            assert cut == pytest.approx(stored, abs=DEVICE_TIMER_ABS_S[name]), name
        elif name in FLOAT32_ROUND_TRIP:
            assert cut == pytest.approx(stored, rel=1e-6), name
        else:
            assert cut == stored, name
    assert body["lap_index"] == lap_index


def test_skipping_the_head_of_a_lap_is_the_same_as_cutting_later(
    client: TestClient, session: Session
) -> None:
    """The plan's protocols read HR over minutes 5-20 of an effort."""
    ride = _evening(session)
    lap = session.exec(select(Lap).where(Lap.ride_id == ride.id, Lap.index == 1)).one()
    start_s, end_s = lap_window_s(ride, lap)

    trimmed = _get(client, f"/activities/{ride.id}/window", lap_index=1, skip_head_s=300)
    explicit = _get(client, f"/activities/{ride.id}/window", start_s=start_s + 300, end_s=end_s)
    whole = _get(client, f"/activities/{ride.id}/window", lap_index=1)

    assert {**trimmed, "lap_index": None} == explicit
    assert trimmed["start_s"] == pytest.approx(whole["start_s"] + 300, abs=1.0)
    assert trimmed["moving_time_s"] == pytest.approx(whole["moving_time_s"] - 300, abs=2.0)


def test_the_window_quotes_the_evaluation_dialogs_figures(
    client: TestClient, session: Session
) -> None:
    ride = _evening(session)
    body = _get(client, f"/activities/{ride.id}/window", start_s=0, end_s=1200)
    assert body["ftp_estimate_w"] is not None
    assert body["baseline_w_per_bpm"] is not None
    assert body["sample_count"] > 1000


def test_a_window_needs_a_lap_or_both_bounds(client: TestClient, session: Session) -> None:
    ride = _evening(session)
    for params in ({}, {"start_s": 10}, {"lap_index": 9}):
        response = client.get(f"/api/v1/activities/{ride.id}/window", params=params, headers=AUTH)
        assert response.status_code == 422, params
        assert "error" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Streams: bounded, never truncated
# ---------------------------------------------------------------------------


def test_streams_are_block_averaged_and_missing_samples_are_null(
    client: TestClient, session: Session
) -> None:
    ride = _evening(session)
    body = _get(
        client,
        f"/activities/{ride.id}/streams",
        start_s=0,
        end_s=600,
        step_s=10,
        channels="power_w,hr_bpm",
    )
    assert body["sample_count"] == len(body["time_s"]) == 60
    assert body["time_s"][:3] == [0.0, 10.0, 20.0]
    assert set(body["channels"]) == {"power_w", "hr_bpm"}
    assert all(len(values) == 60 for values in body["channels"].values())
    # The output is JSON: a NaN would make it invalid, so it must be null.
    assert "NaN" not in json.dumps(body)


def test_an_over_budget_stream_request_is_refused_with_a_suggestion(
    client: TestClient, session: Session
) -> None:
    ride = _evening(session)
    every_channel = "altitude_m,speed_ms,hr_bpm,cadence_rpm,power_w,temperature_c"
    response = client.get(
        f"/api/v1/activities/{ride.id}/streams",
        params={"start_s": 0, "end_s": 3600, "step_s": 1, "channels": every_channel},
        headers=AUTH,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["suggested_step_s"] > 1
    assert 0 < detail["max_window_s_at_this_step"] < 3600

    retry = client.get(
        f"/api/v1/activities/{ride.id}/streams",
        params={
            "start_s": 0,
            "end_s": 3600,
            "step_s": detail["suggested_step_s"],
            "channels": every_channel,
        },
        headers=AUTH,
    )
    assert retry.status_code == 200, "the suggestion must actually fit"


def test_unknown_channels_and_long_windows_are_refused(
    client: TestClient, session: Session
) -> None:
    ride = _evening(session)
    for params in (
        {"start_s": 0, "end_s": 60, "channels": "lat"},
        {"start_s": 0, "end_s": 7200},
        {"start_s": 60, "end_s": 30},
    ):
        response = client.get(f"/api/v1/activities/{ride.id}/streams", params=params, headers=AUTH)
        assert response.status_code == 422, params


# ---------------------------------------------------------------------------
# Routes and fitness
# ---------------------------------------------------------------------------


def test_route_history_is_the_route_table_and_its_summary_card(
    client: TestClient, session: Session
) -> None:
    ride = _evening(session)
    body = _get(client, f"/routes/{ride.route_id}/history")
    rides = session.exec(select(Ride).where(Ride.route_id == ride.route_id)).all()
    summary = route_summary(rides)

    assert [p["activity_id"] for p in body["passes"]] == [r.id for r in rides]
    assert body["fastest_moving_time_s"] == summary.fastest_moving_time_s
    assert body["best_tss"] == summary.best_tss
    first = body["passes"][0]
    for key in (
        "moving_time_s",
        "avg_power_w",
        "normalized_power_w",
        "best_power_1200s_w",
        "avg_hr_bpm",
    ):
        assert first[key] == getattr(ride, key), key
    assert first["bike_name"] == "Ideal Trekking"


def test_routes_are_listed_most_ridden_first(client: TestClient) -> None:
    body = _get(client, "/routes")
    assert body["count"] == 2
    assert {route["ride_count"] for route in body["routes"]} == {1}


def test_fitness_is_the_history_table_and_the_curve_windows(client: TestClient) -> None:
    body = _get(client, "/fitness")
    assert [e["value"] for e in body["evaluations"]] == [176.0, 245.0, 230.0], "newest first"
    assert [e["unit"] for e in body["evaluations"]] == ["bpm", "W", "W"]
    assert body["evaluations"][0]["source"] == "manual"
    assert {w["window_days"] for w in body["power_curves"]} == {30, 90}


def test_curve_window_fields_match_the_power_curve_durations() -> None:
    fields = {
        name for name in schemas.CurveWindowOut.model_fields if name.startswith("best_power_")
    }
    assert fields == {f"best_power_{d}s_w" for d in metrics.POWER_CURVE_WINDOWS_S}


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

# Numeric keys whose meaning carries no unit: identifiers, counts, indices,
# ratios and scales. Everything else must say its unit in its name (rule 12).
UNITLESS_NUMBERS = frozenset(
    {
        "id",
        "activity_id",
        "bike_id",
        "route_id",
        "ride_id",
        "ant_id",
        "index",
        "lap_index",
        "num_laps",
        "ride_count",
        "rides_in_analysis",
        "rides_excluded",
        "count",
        "limit",
        "sample_count",
        "intensity_factor",
        "tss",
        "avg_tss",
        "best_tss",
        "sleep_quality",
        "rpe",
        "perceived_breathing_limit",
        # EvaluationOut: the unit depends on the type and travels in `unit`.
        "value",
    }
)
UNIT_SUFFIXES = (
    "_s",
    "_m",
    "_w",
    "_bpm",
    "_rpm",
    "_ms",
    "_kj",
    "_kcal",
    "_c",
    "_pct",
    "_kg",
    "_mph",
    "_w_per_bpm",
    "_days",
)


def _numeric_properties(spec: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for schema in spec["components"]["schemas"].values():
        for name, prop in schema.get("properties", {}).items():
            types = {prop.get("type")} | {option.get("type") for option in prop.get("anyOf", [])}
            if types & {"integer", "number"}:
                found.add(name)
    return found


def test_every_numeric_key_names_its_unit() -> None:
    spec = create_api().openapi()
    offenders = sorted(
        name
        for name in _numeric_properties(spec)
        if name not in UNITLESS_NUMBERS and not name.endswith(UNIT_SUFFIXES)
    )
    assert offenders == []


def test_the_mcp_contract_snapshot_is_current() -> None:
    """The MCP server documents exactly this contract (rule 46).

    Regenerate with ``uv run python -m bike_tracker.api.v1 > mcp_server/openapi_v1.json``.
    """
    expected = json.dumps(create_api().openapi(), indent=2, sort_keys=True) + "\n"
    assert OPENAPI_SNAPSHOT.read_text() == expected


def test_the_metric_contract_is_the_metric_fields() -> None:
    assert set(schemas.MetricsOut.model_fields) == set(MetricFields.model_fields)
