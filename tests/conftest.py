"""Shared fixtures.

The FIT fixtures are the two real Karoo files in ``docs/``. They are parsed
once per session because decoding 7,928 records twice per test is the slowest
thing in the suite by a wide margin — and the pure-metric tests are supposed
to run in milliseconds.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from bike_tracker.ingest.fit import ParsedRide, parse
from bike_tracker.models import RideTag, Tag

DOCS = Path(__file__).resolve().parent.parent / "docs"

# The trekking-bike ride: 44 minutes, four laps, dual-sided power, and the one
# whose numbers are visible in docs/image.png.
EVENING_FIT = DOCS / "Karoo-Evening_Ride-2026-08-19-1723.fit"
# The road-bike ride: 70 km, single lap, a 29-minute recording pause, and
# single-sided power with no balance channel.
MORNING_FIT = DOCS / "Karoo-Morning_Ride-2025-06-14-0856.fit"

ALL_FIT_FILES = [EVENING_FIT, MORNING_FIT]


@pytest.fixture(scope="session")
def evening_ride() -> ParsedRide:
    return parse(EVENING_FIT.read_bytes())


@pytest.fixture(scope="session")
def morning_ride() -> ParsedRide:
    return parse(MORNING_FIT.read_bytes())


@pytest.fixture(scope="session")
def parsed_rides(evening_ride: ParsedRide, morning_ride: ParsedRide) -> list[ParsedRide]:
    return [evening_ride, morning_ride]


@pytest.fixture
def db_url(tmp_path: Path) -> Iterator[str]:
    """A throwaway database with this app's tables and nothing else.

    Filtered ``create_all`` would be the rule against an unrelated database;
    here every registered model *is* ours, so the whole metadata is correct —
    but the engine is still per-test and disposed afterwards.
    """
    url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    yield url
    engine.dispose()


def tag_ride(
    session: Session, ride_id: int, *, effect: str | None, name: str = "Not training"
) -> Tag:
    """Put a tag on a ride, creating the tag if it does not exist yet.

    Writes the link directly, as the ride-detail handler does; refreshing the
    route is left to the test, which is usually what it is checking.
    """
    tag = Tag(name=name, effect=effect)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    session.add(RideTag(ride_id=ride_id, tag_id=tag.id or 0))
    session.commit()
    session.refresh(tag)
    return tag
