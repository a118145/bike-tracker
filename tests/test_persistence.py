"""Form strings in, correctly typed rows out.

Adapted from the scaffold's CRUD tests to this app's own models. Runs against
a real SQLite file rather than a mock, so the column types Alembic generated
are actually exercised — the point is to prove a form's ``"92.5"`` survives
the round trip as a float and not as a string.

``BikeForm`` is the one form model in the milestone. The bikes dialog itself
is a later milestone, but the model and this path are what the seed script and
the ride-detail bike dropdown already build on.
"""

import pytest
import reflex as rx
from sqlmodel import select

from bike_tracker.ingest.seed import seed_bikes
from bike_tracker.models import Bike, BikeForm, BikeSensor
from bike_tracker.utils.validation import parse_form


def _submit(db_url: str, form: dict[str, str]) -> Bike:
    """The path an on_submit handler takes: cast, validate, insert."""
    parsed, errors = parse_form(form, BikeForm)
    assert errors == {}, errors
    assert parsed is not None

    with rx.session(url=db_url) as session:
        bike = Bike(**parsed.model_dump())
        session.add(bike)
        session.commit()
        session.refresh(bike)
        return bike


def test_create_stores_correct_python_types(db_url: str) -> None:
    created = _submit(db_url, {"name": "Canyon", "weight_kg": "92.5", "notes": "road"})
    assert created.id is not None

    with rx.session(url=db_url) as session:
        stored = session.get(Bike, created.id)

    assert stored is not None
    assert stored.name == "Canyon"
    assert stored.weight_kg == 92.5
    assert isinstance(stored.weight_kg, float)
    assert stored.notes == "road"


def test_blank_optional_persists_as_null(db_url: str) -> None:
    created = _submit(db_url, {"name": "Ideal", "weight_kg": "95", "notes": ""})

    with rx.session(url=db_url) as session:
        stored = session.get(Bike, created.id)

    assert stored is not None
    assert stored.notes is None, "empty input must become NULL, not an empty string"


def test_the_odometer_is_server_owned(db_url: str) -> None:
    """The form must not be able to set it — masterplan §3.1 is explicit."""
    parsed, errors = parse_form({"name": "Canyon", "weight_kg": "92", "odo_m": "999999"}, BikeForm)
    assert errors == {}
    assert not hasattr(parsed, "odo_m")


def test_update_round_trip(db_url: str) -> None:
    created = _submit(db_url, {"name": "Old", "weight_kg": "90"})

    parsed, errors = parse_form({"name": "New", "weight_kg": "88.5"}, BikeForm)
    assert errors == {}
    assert parsed is not None

    with rx.session(url=db_url) as session:
        existing = session.get(Bike, created.id)
        assert existing is not None
        for field, value in parsed.model_dump().items():
            setattr(existing, field, value)
        session.add(existing)
        session.commit()

    with rx.session(url=db_url) as session:
        stored = session.get(Bike, created.id)

    assert stored is not None
    assert stored.name == "New"
    assert stored.weight_kg == 88.5


def test_delete_removes_the_row(db_url: str) -> None:
    created = _submit(db_url, {"name": "Doomed", "weight_kg": "1"})

    with rx.session(url=db_url) as session:
        bike = session.get(Bike, created.id)
        assert bike is not None
        session.delete(bike)
        session.commit()

    with rx.session(url=db_url) as session:
        assert session.get(Bike, created.id) is None
        assert list(session.exec(select(Bike)).all()) == []


def test_invalid_input_never_reaches_the_database(db_url: str) -> None:
    parsed, errors = parse_form({"name": "Bad", "weight_kg": "heavy"}, BikeForm)
    assert parsed is None
    assert "weight_kg" in errors

    with rx.session(url=db_url) as session:
        assert list(session.exec(select(Bike)).all()) == []


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def test_seeding_creates_both_bikes_with_their_sensors(db_url: str) -> None:
    with rx.session(url=db_url) as session:
        seed_bikes(session)
        bikes = {b.name: b for b in session.exec(select(Bike)).all()}
        sensors = session.exec(select(BikeSensor)).all()

    assert set(bikes) == {"Canyon CF SL 7", "Ideal Trekking"}
    ant_ids = {s.ant_id for s in sensors}
    assert ant_ids == {10517, 23828, 13522, 44050}


def test_the_shared_chest_strap_is_registered_on_both_bikes(db_url: str) -> None:
    """One physical strap, two bikes. Recording the truth is what lets
    ``resolve_bike`` know to ignore it."""
    with rx.session(url=db_url) as session:
        seed_bikes(session)
        strap = session.exec(select(BikeSensor).where(BikeSensor.ant_id == 44050)).all()

    assert len({s.bike_id for s in strap}) == 2


def test_seeding_twice_changes_nothing(db_url: str) -> None:
    with rx.session(url=db_url) as session:
        seed_bikes(session)
        first = len(session.exec(select(BikeSensor)).all())
        seed_bikes(session)
        second = len(session.exec(select(BikeSensor)).all())

    assert first == second


def test_seeding_is_idempotent_for_bikes(db_url: str) -> None:
    with rx.session(url=db_url) as session:
        assert len(seed_bikes(session)) == 2
        assert seed_bikes(session) == []


@pytest.mark.parametrize(
    ("name", "weight", "valid"),
    [
        ("Canyon", "92", True),
        ("", "92", False),  # blank name
        ("   ", "92", False),  # whitespace-only name
        ("Canyon", "-5", False),  # negative weight
        ("Canyon", "", False),  # required field left empty
    ],
)
def test_form_validation_rules(name: str, weight: str, valid: bool) -> None:
    parsed, errors = parse_form({"name": name, "weight_kg": weight}, BikeForm)
    assert (parsed is not None) is valid
    assert (errors == {}) is valid
