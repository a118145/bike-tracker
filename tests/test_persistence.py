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
from sqlmodel import Session, create_engine, select

from bike_tracker.ingest.importer import recalculate_odometer
from bike_tracker.ingest.seed import seed_bikes
from bike_tracker.ingest.sources.file import import_file
from bike_tracker.models import (
    TAG_EFFECT_EXCLUDE,
    Bike,
    BikeForm,
    BikeSensor,
    Ride,
    RideTag,
    TagForm,
    analysis_rides,
    excluded_ride_ids,
)
from bike_tracker.utils.validation import parse_form

from .conftest import EVENING_FIT, tag_ride


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


# ---------------------------------------------------------------------------
# Bike management (masterplan §2.6)
# ---------------------------------------------------------------------------


def test_recalculating_the_odometer_matches_the_rides(db_url: str) -> None:
    """The repair for the drift the incremental updates accumulate."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        import_file(EVENING_FIT, session)
        ride = session.exec(select(Ride)).one()
        bike = session.get(Bike, ride.bike_id)
        assert bike is not None and bike.id is not None

        # Simulate the drift a reassignment sequence leaves behind.
        bike.odo_m = 0.0
        session.add(bike)
        session.commit()

        assert recalculate_odometer(session, bike.id) == pytest.approx(ride.distance_m)
        session.refresh(bike)
        assert bike.odo_m == pytest.approx(ride.distance_m)
    engine.dispose()


def test_recalculating_a_bike_with_no_rides_gives_zero(db_url: str) -> None:
    """SUM over no rows is NULL, and `odo_m` is not nullable."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        bike = Bike(name="Unused", weight_kg=10.0, odo_m=999.0)
        session.add(bike)
        session.commit()
        session.refresh(bike)
        assert bike.id is not None

        assert recalculate_odometer(session, bike.id) == 0.0
        session.refresh(bike)
        assert bike.odo_m == 0.0
    engine.dispose()


def test_recalculating_a_missing_bike_is_harmless(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        assert recalculate_odometer(session, 999) == 0.0
    engine.dispose()


def test_deleting_a_bike_unassigns_its_rides_and_drops_its_sensors(db_url: str) -> None:
    """The database has no cascade — SQLite is not even enforcing the keys.

    Dropping the sensors is the part that matters: ``resolve_bike`` queries
    ``BikeSensor`` by ``ant_id`` alone and never joins ``bike``, so an orphaned
    row would keep voting for a bike that no longer exists.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        import_file(EVENING_FIT, session)
        ride = session.exec(select(Ride)).one()
        bike_id = ride.bike_id
        assert bike_id is not None

        # What BikesState.confirm_delete does, in one transaction.
        for attached in session.exec(select(Ride).where(Ride.bike_id == bike_id)).all():
            attached.bike_id = None
            session.add(attached)
        for sensor in session.exec(select(BikeSensor).where(BikeSensor.bike_id == bike_id)).all():
            session.delete(sensor)
        session.delete(session.get(Bike, bike_id))
        session.commit()

        session.refresh(ride)
        assert ride.bike_id is None, "the ride itself must survive"
        assert ride.distance_m > 0, "only the link is lost, not the data"
        assert session.get(Bike, bike_id) is None
        assert session.exec(select(BikeSensor).where(BikeSensor.bike_id == bike_id)).all() == []
    engine.dispose()


def test_orphaned_sensors_would_assign_rides_to_a_deleted_bike(db_url: str) -> None:
    """Why ``confirm_delete`` must remove the sensors, not just the bike.

    ``resolve_bike`` looks ``BikeSensor`` up by ``ant_id`` alone and never
    joins ``bike``. Deleting a bike while leaving its sensors behind therefore
    leaves them voting for an id that no longer resolves — this test pins that
    hazard so the cleanup is not "simplified" away later.
    """
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        trekking = session.exec(select(Bike).where(Bike.name == "Ideal Trekking")).one()
        orphan_id = trekking.id

        session.delete(trekking)  # sensors deliberately left behind
        session.commit()

        result = import_file(EVENING_FIT, session)
        ride = session.get(Ride, result.ride_id)
        assert ride is not None
        assert ride.bike_id == orphan_id
        assert session.get(Bike, ride.bike_id) is None, "points at a bike that is gone"
    engine.dispose()


def test_deleting_the_sensors_too_keeps_every_assignment_resolvable(db_url: str) -> None:
    """With the full cleanup, a ride is never left pointing at nothing."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        trekking = session.exec(select(Bike).where(Bike.name == "Ideal Trekking")).one()

        for sensor in session.exec(
            select(BikeSensor).where(BikeSensor.bike_id == trekking.id)
        ).all():
            session.delete(sensor)
        session.delete(trekking)
        session.commit()

        result = import_file(EVENING_FIT, session)
        ride = session.get(Ride, result.ride_id)
        assert ride is not None
        # The shared chest strap now belongs to exactly one bike, so it becomes
        # decisive — which is correct inference from what the database knows.
        # The guarantee that matters is that whatever it resolves to exists.
        if ride.bike_id is not None:
            assert session.get(Bike, ride.bike_id) is not None
    engine.dispose()


def test_the_shared_strap_alone_still_does_not_identify_a_bike(db_url: str) -> None:
    """Deleting one bike must not make the shared strap decisive for the other."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        seed_bikes(session)
        canyon = session.exec(select(Bike).where(Bike.name == "Canyon CF SL 7")).one()

        # Strip the trekking bike's power meter, leaving only the shared strap.
        trekking = session.exec(select(Bike).where(Bike.name == "Ideal Trekking")).one()
        for sensor in session.exec(
            select(BikeSensor)
            .where(BikeSensor.bike_id == trekking.id)
            .where(BikeSensor.ant_id == 13522)
        ).all():
            session.delete(sensor)
        session.commit()

        result = import_file(EVENING_FIT, session)
        ride = session.get(Ride, result.ride_id)
        assert ride is not None
        assert ride.bike_id is None
        assert ride.bike_id != canyon.id
    engine.dispose()


# ---------------------------------------------------------------------------
# Excluding a ride from analysis (AGENTS.md rules 42 and 43)
# ---------------------------------------------------------------------------


def _import_one(session: Session) -> Ride:
    seed_bikes(session)
    import_file(EVENING_FIT, session)
    return session.exec(select(Ride)).one()


def _analysed(session: Session) -> list[int | None]:
    return [r.id for r in session.exec(analysis_rides()).all()]


def test_a_ride_with_an_excluding_tag_is_not_in_analysis(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride = _import_one(session)
        ride_id = ride.id or 0
        tag = tag_ride(session, ride_id, effect=TAG_EFFECT_EXCLUDE)
        assert _analysed(session) == []
        assert excluded_ride_ids(session, [ride_id]) == {ride_id}

        session.delete(session.get(RideTag, (ride_id, tag.id)))
        session.commit()
        assert _analysed(session) == [ride_id]
        assert excluded_ride_ids(session, [ride_id]) == set()
    engine.dispose()


def test_a_tag_without_an_effect_changes_nothing(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride = _import_one(session)
        tag_ride(session, ride.id or 0, effect=None, name="Commute")
        assert _analysed(session) == [ride.id]
    engine.dispose()


def test_the_effect_decides_and_the_name_does_not(db_url: str) -> None:
    """Renaming is cosmetic; switching the effect is what moves the ride."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride = _import_one(session)
        ride_id = ride.id or 0
        tag = tag_ride(session, ride_id, effect=TAG_EFFECT_EXCLUDE)

        tag.name = "Anything else"
        session.add(tag)
        session.commit()
        assert _analysed(session) == [], "a rename changed which rides count"

        tag.effect = None
        session.add(tag)
        session.commit()
        assert _analysed(session) == [ride_id]
    engine.dispose()


def test_an_excluded_ride_still_counts_on_the_odometer(db_url: str) -> None:
    """Excluding says the data is unrepresentative, not that the ride did not
    happen: it still wore the bike."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride = _import_one(session)
        tag_ride(session, ride.id or 0, effect=TAG_EFFECT_EXCLUDE)
        assert ride.bike_id is not None
        assert recalculate_odometer(session, ride.bike_id) == pytest.approx(ride.distance_m)
    engine.dispose()


# ---------------------------------------------------------------------------
# Tag form
# ---------------------------------------------------------------------------


def test_the_tag_form_trims_the_name_and_maps_no_effect_to_none() -> None:
    parsed, errors = parse_form({"name": "  Commute ", "color": "teal", "effect": ""}, TagForm)
    assert errors == {}
    assert parsed is not None
    assert parsed.name == "Commute"
    assert parsed.effect is None


@pytest.mark.parametrize(
    ("form", "field"),
    [
        ({"name": "   ", "color": "gray", "effect": ""}, "name"),
        ({"name": "x" * 41, "color": "gray", "effect": ""}, "name"),
        ({"name": "Commute", "color": "chartreuse", "effect": ""}, "color"),
        ({"name": "Commute", "color": "gray", "effect": "delete_ride"}, "effect"),
    ],
)
def test_the_tag_form_rejects(form: dict[str, str], field: str) -> None:
    parsed, errors = parse_form(form, TagForm)
    assert parsed is None
    assert field in errors
