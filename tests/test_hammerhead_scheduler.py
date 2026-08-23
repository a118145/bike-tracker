"""The poll timer's wiring (AGENTS.md rule 14, brief §3.2).

Not an end-to-end test of APScheduler — it is a mature library and its
triggers are its own business. What is worth pinning is the four decisions
this app makes about *when* a job should exist at all, each of which is a
one-line condition that would be silently wrong in the opposite direction:

* enabled and allowed  → a job, at the stored interval
* disabled             → no job, and an existing one removed
* dev mode             → no job, whatever the flag says
* dev mode + override  → a job again

The dev-mode guard is the one that matters day to day: ``reflex run`` restarts
the backend on every file save, so without it a burst of third-party API calls
goes out every time a line is edited.
"""

from collections.abc import Iterator

import pytest
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bike_tracker.ingest import scheduler as sched


@pytest.fixture
def running() -> Iterator[AsyncIOScheduler]:
    """A scheduler that is never started.

    ``add_job`` and ``remove_job`` work on a stopped scheduler; starting one
    would need a running event loop and would make the test fire real polls.
    """
    instance = AsyncIOScheduler()
    sched._scheduler = instance
    yield instance
    sched._scheduler = None


def _interval_minutes(job: Job) -> float:
    trigger = job.trigger
    assert isinstance(trigger, IntervalTrigger)
    return trigger.interval.total_seconds() / 60


def test_enabling_adds_a_job_at_the_stored_interval(
    running: AsyncIOScheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "poll_allowed_here", lambda: True)

    sched.apply_settings(enabled=True, interval_min=15)

    job = running.get_job(sched.JOB_ID)
    assert job is not None
    assert _interval_minutes(job) == 15


def test_changing_the_interval_reschedules_rather_than_duplicates(
    running: AsyncIOScheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding a second job would poll twice per period, and the extra one
    would outlive every attempt to change the interval again."""
    monkeypatch.setattr(sched, "poll_allowed_here", lambda: True)

    sched.apply_settings(enabled=True, interval_min=30)
    sched.apply_settings(enabled=True, interval_min=5)

    assert len(running.get_jobs()) == 1
    assert _interval_minutes(running.get_jobs()[0]) == 5


def test_disabling_removes_the_job(
    running: AsyncIOScheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisting the flag without removing the job leaves the two
    disagreeing until the next restart."""
    monkeypatch.setattr(sched, "poll_allowed_here", lambda: True)
    sched.apply_settings(enabled=True, interval_min=30)

    sched.apply_settings(enabled=False, interval_min=30)

    assert running.get_job(sched.JOB_ID) is None


def test_dev_mode_never_schedules_however_the_flag_is_set(
    running: AsyncIOScheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "poll_allowed_here", lambda: False)

    sched.apply_settings(enabled=True, interval_min=30)

    assert running.get_job(sched.JOB_ID) is None


def test_a_zero_interval_cannot_produce_a_hot_loop(
    running: AsyncIOScheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IntervalTrigger(minutes=0) fires continuously. The database column is
    an integer with no constraint, so the floor lives here."""
    monkeypatch.setattr(sched, "poll_allowed_here", lambda: True)

    sched.apply_settings(enabled=True, interval_min=0)

    job = running.get_job(sched.JOB_ID)
    assert job is not None
    assert _interval_minutes(job) == 1


def test_nothing_happens_when_no_scheduler_is_running() -> None:
    """The Settings page calls this whether or not the lifespan task ran —
    it does not in dev mode — so it must be a no-op, not an AttributeError."""
    sched._scheduler = None
    sched.apply_settings(enabled=True, interval_min=10)  # must not raise


# ---------------------------------------------------------------------------
# The environment guard itself
# ---------------------------------------------------------------------------


def test_the_override_re_enables_polling_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIKE_TRACKER_POLL_IN_DEV", "1")
    assert sched.poll_allowed_here()


def test_without_the_override_dev_mode_does_not_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests run under REFLEX_ENV_MODE=dev, which is the point."""
    monkeypatch.delenv("BIKE_TRACKER_POLL_IN_DEV", raising=False)
    assert not sched.poll_allowed_here()
