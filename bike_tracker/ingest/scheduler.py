"""The poll timer (brief §3.2, AGENTS.md rule 14).

**Not** an ``@rx.event(background=True)``. Such a task lives exactly as long
as the browser session that started it, which for a half-hourly poller is the
wrong lifetime by every measure: it stops when a tab closes and multiplies
when three tabs are open. This hangs off the backend lifecycle instead, so it
starts and dies with the process.

Two guards that only matter in practice:

* **Dev mode does not poll.** ``reflex run`` restarts the backend on every
  file save, so a poller wired naively to the lifespan fires a burst of API
  calls at the third-party service every time a line is edited. Override with
  ``BIKE_TRACKER_POLL_IN_DEV=1`` when the poller itself is what is being
  worked on.
* **The tick runs in a thread.** A poll parses FIT files and writes to
  SQLite; doing that on the event loop stalls every websocket in the app for
  as long as it takes.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import reflex as rx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from reflex.config import environment
from reflex.constants import Env

from ..models import get_account
from ..utils.logging import get_logger
from .hammerhead_sync import run_poll_once

log = get_logger(__name__)

JOB_ID = "hammerhead-poll"

_scheduler: AsyncIOScheduler | None = None


def poll_allowed_here() -> bool:
    """Whether this process may run the timer at all.

    Environment beats the enabled flag: a developer with real credentials in
    their shell should not have to remember to switch the poller off before
    editing.
    """
    if os.environ.get("BIKE_TRACKER_POLL_IN_DEV", "").strip() in ("1", "true", "yes"):
        return True
    return environment.REFLEX_ENV_MODE.get() != Env.DEV


async def _tick() -> None:
    """One scheduled poll. Never raises — a raising job kills the schedule."""
    try:
        result = await asyncio.to_thread(run_poll_once)
    # Bare `Exception` on purpose: an APScheduler job that raises is dropped
    # from the schedule, so one bad tick would silently end the poller.
    except Exception as exc:
        log.exception("hammerhead.tick.crashed", error=str(exc))
        return
    log.info("hammerhead.tick", ok=result.ok, message=result.message)


def apply_settings(*, enabled: bool, interval_min: int) -> None:
    """Add, reschedule or remove the job to match the stored settings.

    Called from the Settings page as well as at startup, so changing the
    interval takes effect without a restart.
    """
    if _scheduler is None:
        return

    existing = _scheduler.get_job(JOB_ID)
    if not (enabled and poll_allowed_here()):
        if existing is not None:
            _scheduler.remove_job(JOB_ID)
            log.info("hammerhead.schedule.removed")
        return

    trigger = IntervalTrigger(minutes=max(interval_min, 1))
    if existing is None:
        _scheduler.add_job(_tick, trigger, id=JOB_ID, max_instances=1, coalesce=True)
        log.info("hammerhead.schedule.added", interval_min=interval_min)
    else:
        _scheduler.reschedule_job(JOB_ID, trigger=trigger)
        log.info("hammerhead.schedule.rescheduled", interval_min=interval_min)


@asynccontextmanager
async def poller_lifespan() -> AsyncIterator[None]:
    """Lifespan task: start the scheduler with the app, stop it with the app.

    Registered in ``bike_tracker.py``. Reflex runs lifespan tasks on the
    backend's own loop, which is exactly the lifetime wanted here.
    """
    global _scheduler

    if not poll_allowed_here():
        log.info("hammerhead.schedule.skipped", reason="dev mode")
        yield
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.start()
    try:
        with rx.session() as session:
            account = get_account(session)
            enabled, interval = account.enabled, account.poll_interval_min
        apply_settings(enabled=enabled, interval_min=interval)
    # Never let a scheduling problem stop the app from serving pages.
    except Exception as exc:
        log.exception("hammerhead.schedule.start_failed", error=str(exc))

    try:
        yield
    finally:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("hammerhead.schedule.stopped")
