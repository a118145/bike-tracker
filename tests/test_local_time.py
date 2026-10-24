"""Stored timestamps are naive UTC; people and training weeks live locally.

The rider is in Europe/Berlin. A 07:30 commute is stored as 05:30, and a ride
started at 00:30 on a Monday in summer is stored as Sunday 22:30 — the wrong
day and the wrong training week if shown as stored. The dates are written out
rather than computed, so a broken conversion cannot also break the
expectation.
"""

from datetime import date, datetime

import pytest

from bike_tracker.utils.formatting import format_date, format_datetime
from bike_tracker.utils.local_time import local_date, to_local


@pytest.fixture(autouse=True)
def _berlin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Berlin")


def test_a_morning_commute_shows_its_local_clock_time() -> None:
    stored = datetime(2026, 9, 28, 5, 30)  # CEST is UTC+2
    assert format_datetime(stored, "%H:%M") == "07:30"


def test_a_ride_after_local_midnight_belongs_to_the_next_day() -> None:
    stored = datetime(2026, 9, 27, 22, 30)  # Sunday UTC, Monday 00:30 locally
    assert local_date(stored) == date(2026, 9, 28)
    assert format_date(stored, "%A") == "Monday"


def test_winter_time_is_one_hour_not_two() -> None:
    assert to_local(datetime(2026, 12, 7, 6, 0)).strftime("%H:%M") == "07:00"


def test_a_plain_date_is_not_shifted() -> None:
    assert format_date(date(2026, 9, 28)) == "2026-09-28"


def test_an_unknown_zone_falls_back_to_berlin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Mars/Olympus_Mons")
    assert format_datetime(datetime(2026, 9, 28, 5, 30), "%H:%M") == "07:30"
