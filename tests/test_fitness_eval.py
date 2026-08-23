"""An evaluation changes later rides, never earlier ones.

This is brief §5.2 made testable. `ftp_at_time_w` exists so that raising FTP
does not rewrite the training load of every past ride — and the failure mode
is silent: every number still looks plausible, the winter CTL curve is simply
wrong. No single function shows it, because the invariant is about which
*rows* a refresh touches.

Runs against a real SQLite file rather than fakes, so the "newest evaluation
at or before this ride" query is the one Alembic's schema actually indexes.
"""

from datetime import datetime, timedelta

import reflex as rx
from sqlmodel import Session, select

from bike_tracker.ingest.importer import refresh_ftp_dependent
from bike_tracker.metrics import intensity_factor, tss
from bike_tracker.models import EVAL_FTP, FitnessEval, Lap, Ride

# Three rides a fortnight apart, all with the same power profile so any
# difference between them can only come from the FTP they were scored against.
BASE = datetime(2026, 5, 1, 9, 0)
NP_W = 240.0
MOVING_S = 3600.0


def _add_ride(session: Session, when: datetime, ftp_w: int, device_ftp_w: int | None = 200) -> int:
    ride = Ride(
        name=f"Ride {when:%d %b}",
        start_time=when,
        end_time=when + timedelta(hours=1),
        fit_hash=f"hash-{when.isoformat()}",
        moving_time_s=MOVING_S,
        elapsed_time_s=MOVING_S,
        normalized_power_w=int(NP_W),
        power_available=True,
        ftp_at_time_w=ftp_w,
        device_ftp_w=device_ftp_w,
    )
    session.add(ride)
    session.commit()
    session.refresh(ride)

    lap = Lap(
        ride_id=ride.id,
        index=0,
        start_time=when,
        end_time=when + timedelta(hours=1),
        moving_time_s=MOVING_S,
        elapsed_time_s=MOVING_S,
        normalized_power_w=int(NP_W),
        power_available=True,
        ftp_at_time_w=ftp_w,
    )
    session.add(lap)
    session.commit()
    return ride.id or 0


def _seed(db_url: str) -> tuple[int, int, int]:
    with rx.session(url=db_url) as session:
        early = _add_ride(session, BASE, ftp_w=200)
        middle = _add_ride(session, BASE + timedelta(days=14), ftp_w=200)
        late = _add_ride(session, BASE + timedelta(days=28), ftp_w=200)
    return early, middle, late


def _ftp_of(db_url: str, ride_id: int) -> int | None:
    with rx.session(url=db_url) as session:
        ride = session.get(Ride, ride_id)
        assert ride is not None
        return ride.ftp_at_time_w


def _save_eval(db_url: str, ride_id: int, when: datetime, value: float) -> int:
    with rx.session(url=db_url) as session:
        record = FitnessEval(
            type=EVAL_FTP,
            value=value,
            valid_from=when,
            ride_id=ride_id,
            start_time=when,
            end_time=when + timedelta(minutes=20),
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        refresh_ftp_dependent(session, when)
        return record.id or 0


def _delete_eval(db_url: str, eval_id: int) -> None:
    with rx.session(url=db_url) as session:
        record = session.get(FitnessEval, eval_id)
        assert record is not None
        valid_from = record.valid_from
        session.delete(record)
        session.commit()
        refresh_ftp_dependent(session, valid_from)


def test_an_evaluation_updates_its_own_ride_and_every_later_one(db_url: str) -> None:
    early, middle, late = _seed(db_url)

    _save_eval(db_url, middle, BASE + timedelta(days=14), value=280.0)

    # The ride it came from and the one after it move to the new FTP...
    assert _ftp_of(db_url, middle) == 280
    assert _ftp_of(db_url, late) == 280
    # ...and the one before it does not. This is the whole point of the field.
    assert _ftp_of(db_url, early) == 200


def test_the_load_is_rescored_against_the_new_ftp(db_url: str) -> None:
    _early, middle, _late = _seed(db_url)
    _save_eval(db_url, middle, BASE + timedelta(days=14), value=280.0)

    expected_if = intensity_factor(NP_W, 280)
    with rx.session(url=db_url) as session:
        ride = session.get(Ride, middle)
        assert ride is not None
        assert ride.intensity_factor is not None
        assert abs(ride.intensity_factor - expected_if) < 1e-9
        assert ride.tss is not None
        assert abs(ride.tss - tss(NP_W, expected_if, MOVING_S)) < 1e-9


def test_laps_are_rescored_with_their_ride(db_url: str) -> None:
    """A lap repeats its ride's FTP by design, so it has to move with it."""
    _early, middle, _late = _seed(db_url)
    _save_eval(db_url, middle, BASE + timedelta(days=14), value=280.0)

    with rx.session(url=db_url) as session:
        laps = session.exec(select(Lap).where(Lap.ride_id == middle)).all()
        assert laps
        assert all(lap.ftp_at_time_w == 280 for lap in laps)
        assert all(lap.tss is not None for lap in laps)


def test_a_later_evaluation_does_not_reach_back_past_an_earlier_one(db_url: str) -> None:
    """Each evaluation owns the span up to the next one, not the whole history."""
    early, middle, late = _seed(db_url)
    _save_eval(db_url, early, BASE, value=250.0)
    _save_eval(db_url, late, BASE + timedelta(days=28), value=300.0)

    assert _ftp_of(db_url, early) == 250
    assert _ftp_of(db_url, middle) == 250  # still governed by the first
    assert _ftp_of(db_url, late) == 300


def test_deleting_an_evaluation_puts_the_rides_back(db_url: str) -> None:
    early, middle, late = _seed(db_url)
    _save_eval(db_url, early, BASE, value=250.0)
    eval_id = _save_eval(db_url, middle, BASE + timedelta(days=14), value=280.0)
    assert _ftp_of(db_url, late) == 280

    with rx.session(url=db_url) as session:
        record = session.get(FitnessEval, eval_id)
        assert record is not None
        valid_from = record.valid_from
        session.delete(record)
        session.commit()
        refresh_ftp_dependent(session, valid_from)

    # Back to the earlier evaluation, not to nothing.
    assert _ftp_of(db_url, middle) == 250
    assert _ftp_of(db_url, late) == 250
    assert _ftp_of(db_url, early) == 250


def test_deleting_one_of_several_falls_back_to_the_earlier_evaluation(db_url: str) -> None:
    """First fallback step: an earlier evaluation, not the device value."""
    early, _middle, late = _seed(db_url)
    _save_eval(db_url, early, BASE, value=250.0)
    later_id = _save_eval(db_url, late, BASE + timedelta(days=28), value=300.0)
    assert _ftp_of(db_url, late) == 300

    _delete_eval(db_url, later_id)

    # Back to the earlier evaluation — 250, not the device's 200.
    assert _ftp_of(db_url, late) == 250


def test_deleting_the_last_evaluation_falls_back_to_the_device(db_url: str) -> None:
    """Second fallback step, and the bug this replaced.

    The old behaviour kept whatever the column already held, so a ride went on
    quoting the number from an evaluation that no longer existed.
    """
    early, _middle, _late = _seed(db_url)
    eval_id = _save_eval(db_url, early, BASE, value=250.0)
    assert _ftp_of(db_url, early) == 250

    _delete_eval(db_url, eval_id)

    # The head unit's own figure, NOT the deleted evaluation's 250.
    assert _ftp_of(db_url, early) == 200
    with rx.session(url=db_url) as session:
        ride = session.get(Ride, early)
        assert ride is not None
        assert ride.tss is not None, "a device FTP still scores the ride"


def test_with_no_evaluation_and_no_device_value_the_ftp_is_unset(db_url: str) -> None:
    """Third step: nothing. A stale score is worse than an honest blank."""
    with rx.session(url=db_url) as session:
        ride_id = _add_ride(session, BASE, ftp_w=250, device_ftp_w=None)

    with rx.session(url=db_url) as session:
        refresh_ftp_dependent(session, BASE)

    assert _ftp_of(db_url, ride_id) is None
    with rx.session(url=db_url) as session:
        ride = session.get(Ride, ride_id)
        assert ride is not None
        assert ride.tss is None
        assert ride.intensity_factor is None


def test_lthr_has_no_device_step(db_url: str) -> None:
    """The Karoo writes no threshold heart rate.

    `max_heart_rate` is a different quantity and is deliberately not
    substituted, so an LTHR with no evaluation behind it stays unset.
    """
    with rx.session(url=db_url) as session:
        ride_id = _add_ride(session, BASE, ftp_w=250)
        ride = session.get(Ride, ride_id)
        assert ride is not None
        ride.lthr_at_time_bpm = 165
        session.add(ride)
        session.commit()

    with rx.session(url=db_url) as session:
        refresh_ftp_dependent(session, BASE)

    with rx.session(url=db_url) as session:
        ride = session.get(Ride, ride_id)
        assert ride is not None
        assert ride.lthr_at_time_bpm is None
        assert ride.device_lthr_bpm is None
