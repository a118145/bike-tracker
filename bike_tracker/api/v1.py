"""``/api/v1`` — read-only REST routes, mounted into Reflex's backend.

``rx.App(api_transformer=create_api())`` puts this FastAPI app in front of
Reflex's own ASGI app; Reflex wraps both in its lifespan app, so the poller
still starts with the backend (``reflex/app.py``, 0.9.8).

The routes are thin: parse the query, open a session, hand off to
``queries``. Every route requires the bearer token (``auth``). The interactive
docs and the OpenAPI document are switched off — they would be served
unauthenticated on the GUI's host; the contract lives in
``mcp_server/openapi_v1.json`` instead, regenerated from :func:`create_api`.

Sync handlers on purpose: FastAPI runs them in a thread pool, which is where
SQLite reads and numpy belong rather than on the event loop.
"""

import json
import os
import sys
from datetime import UTC, date, datetime
from typing import Annotated

import reflex as rx
from fastapi import APIRouter, Depends, FastAPI, Query

from . import queries, schemas
from .auth import require_token

API_PREFIX = "/api/v1"

router = APIRouter(dependencies=[Depends(require_token)])


def _app_version() -> str:
    """Set from the release tag at image build time; ``dev`` elsewhere."""
    return os.environ.get("APP_VERSION", "").strip() or "dev"


@router.get("/version")
def get_version() -> schemas.VersionOut:
    with rx.session() as session:
        return queries.version(session, _app_version())


@router.get("/athlete")
def get_athlete() -> schemas.AthleteOut:
    with rx.session() as session:
        return queries.athlete(session)


@router.get("/activities")
def list_activities(
    from_date: date | None = None,
    to_date: date | None = None,
    bike_id: int | None = None,
    route_id: int | None = None,
    tag: str | None = None,
    include_excluded: bool = False,
    # ~600 characters a row: 100 rows stay well inside the connector budget.
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> schemas.ActivitiesOut:
    with rx.session() as session:
        return queries.list_activities(
            session,
            from_date=from_date,
            to_date=to_date,
            bike_id=bike_id,
            route_id=route_id,
            tag=tag,
            include_excluded=include_excluded,
            limit=limit,
        )


@router.get("/activities/{activity_id}")
def get_activity(activity_id: int) -> schemas.ActivityOut:
    with rx.session() as session:
        return queries.activity(session, activity_id)


@router.get("/activities/{activity_id}/window")
def get_window(
    activity_id: int,
    lap_index: Annotated[int | None, Query(ge=0)] = None,
    start_s: Annotated[float | None, Query(ge=0)] = None,
    end_s: Annotated[float | None, Query(ge=0)] = None,
    skip_head_s: Annotated[float, Query(ge=0)] = 0.0,
    skip_tail_s: Annotated[float, Query(ge=0)] = 0.0,
) -> schemas.WindowOut:
    with rx.session() as session:
        return queries.window(
            session,
            activity_id,
            lap_index=lap_index,
            start_s=start_s,
            end_s=end_s,
            skip_head_s=skip_head_s,
            skip_tail_s=skip_tail_s,
        )


@router.get("/activities/{activity_id}/streams")
def get_streams(
    activity_id: int,
    start_s: Annotated[float, Query(ge=0)],
    end_s: Annotated[float, Query(gt=0)],
    step_s: Annotated[int, Query(ge=1, le=300)] = 5,
    channels: Annotated[str, Query()] = "power_w,hr_bpm,cadence_rpm",
) -> schemas.StreamsOut:
    names = [name.strip() for name in channels.split(",") if name.strip()]
    with rx.session() as session:
        return queries.stream_window(
            session, activity_id, start_s=start_s, end_s=end_s, step_s=step_s, channels=names
        )


@router.get("/routes")
def list_routes() -> schemas.RoutesOut:
    with rx.session() as session:
        return queries.list_routes(session)


@router.get("/routes/{route_id}/history")
def get_route_history(route_id: int, include_excluded: bool = True) -> schemas.RouteHistoryOut:
    with rx.session() as session:
        return queries.route_history(session, route_id, include_excluded=include_excluded)


@router.get("/fitness")
def get_fitness() -> schemas.FitnessOut:
    with rx.session() as session:
        return queries.fitness(session, now=datetime.now(UTC).replace(tzinfo=None))


def create_api() -> FastAPI:
    api = FastAPI(
        title="bike-tracker",
        version=queries.API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    api.include_router(router, prefix=API_PREFIX)
    return api


if __name__ == "__main__":
    # Writes the contract the MCP server documents (AGENTS.md rule 46):
    #   uv run python -m bike_tracker.api.v1 > mcp_server/openapi_v1.json
    sys.stdout.write(json.dumps(create_api().openapi(), indent=2, sort_keys=True) + "\n")
