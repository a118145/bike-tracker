"""The Karoo API as a ride source (masterplan §2.6.2, brief §3.2).

Same contract as ``sources/file.py``: bring bytes, call
``importer.import_ride``, reimplement none of the import sequence
(AGENTS.md rule 10). Everything specific to this source is about *which*
activities to fetch, never about what happens to them afterwards.

Three things here are load-bearing and none of them is obvious:

* **Deduplication happens before the download, not after.** ``import_ride``
  already refuses a duplicate by ``fit_hash`` — but only once the bytes are
  on the wire. Filtering on the activity id first turns a poll over 200 known
  rides into one request instead of 201.
* **A ride imported by hand gets stamped with its activity id** the first
  time the poller meets it. Without that it is never in the id set, so it is
  re-downloaded on every poll for ever, silently, and only the bandwidth
  shows it.
* **The cursor only advances when the window is finished.** A run cut short
  by the download cap must leave the cursor where it was, or the rides it did
  not reach are skipped permanently.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlmodel import Session, col, select

from ...models import Ride
from ...utils.logging import get_logger
from ..fit import FitParseError, parse
from ..hammerhead_client import (
    ActivitySummary,
    HammerheadAuthError,
    HammerheadClient,
    HammerheadError,
)
from ..importer import import_ride

log = get_logger(__name__)

SOURCE = "hammerhead"

# The Karoo uploads when it finds wifi, so an activity can appear days after
# it was ridden — with a createdAt behind wherever the cursor got to. Listing
# a few extra days costs one request and the id filter discards them.
CURSOR_OVERLAP_DAYS = 3

# A first backfill of several seasons should not be one run holding a thread
# for twenty minutes. Whatever is left is picked up on the next tick, because
# the cursor does not move while the cap is in force.
MAX_DOWNLOADS_PER_RUN = 25

# Hard stop on the list walk. The spec caps perPage at 100, so this is 5000
# activities — far past anything real, and it means a server that reports a
# wrong totalPages cannot spin the poller for ever.
MAX_PAGES = 50


@dataclass
class PollOutcome:
    """What one poll did, for the status line and the log."""

    imported: int = 0
    # Activities already held, filtered out without a download.
    skipped: int = 0
    # (activity_id, message) — one bad file must not cost the other forty.
    failures: list[tuple[str, str]] = field(default_factory=list)
    # True when the download cap stopped the run early, so the cursor stays.
    truncated: bool = False
    # Where the cursor should move to, or None to leave it alone.
    next_cursor: date | None = None

    @property
    def failed(self) -> int:
        return len(self.failures)


def poll(
    session: Session,
    client: HammerheadClient,
    *,
    since: date | None,
    max_downloads: int = MAX_DOWNLOADS_PER_RUN,
) -> PollOutcome:
    """Fetch and import every activity not already held.

    Args:
        session: An open session. The caller owns the transaction boundary,
            as everywhere else in ``ingest``.
        client: An authenticated client. Token refresh is its business.
        since: The ``startDate`` to ask for, or ``None`` for everything.
        max_downloads: Cap for this run. Exposed so a test does not need 26
            fixtures to exercise truncation.

    Returns:
        A :class:`PollOutcome`. Never raises for a per-activity problem.

    Raises:
        HammerheadAuthError: The connection is dead. Propagated deliberately —
            the caller switches the poller off, which no per-activity failure
            should ever do.
        HammerheadApiError: The activity *list* could not be read, so it is
            not known what is missing. A failure downloading one file is
            recorded instead.
    """
    activities = _list_all(client, since)
    outcome = PollOutcome()
    if not activities:
        log.info("hammerhead.poll.empty", since=str(since))
        outcome.next_cursor = since
        return outcome

    known = _known_external_ids(session)
    pending = [a for a in activities if a.id not in known]
    outcome.skipped = len(activities) - len(pending)

    if len(pending) > max_downloads:
        pending = pending[:max_downloads]
        outcome.truncated = True

    for activity in pending:
        _ingest_one(session, client, activity, outcome)

    # Only a run that saw its whole window through may move the watermark, and
    # even then it steps back far enough to catch a late upload.
    if not outcome.truncated:
        newest = max(a.created_at for a in activities).date()
        outcome.next_cursor = newest - timedelta(days=CURSOR_OVERLAP_DAYS)

    log.info(
        "hammerhead.poll",
        listed=len(activities),
        imported=outcome.imported,
        skipped=outcome.skipped,
        failed=outcome.failed,
        truncated=outcome.truncated,
    )
    return outcome


def _ingest_one(
    session: Session,
    client: HammerheadClient,
    activity: ActivitySummary,
    outcome: PollOutcome,
) -> None:
    """Download and import one activity, converting failure into a record.

    ``HammerheadAuthError`` is the one exception allowed through: it means
    every remaining download would fail the same way, so continuing would just
    produce forty identical failures and an unusable status line.
    """
    try:
        data = client.download_fit(activity.id)
    except HammerheadAuthError:
        # Ahead of the broad clause below, and not merged into it: every
        # remaining download would fail identically, so recording forty copies
        # of one message instead of stopping makes the status line useless.
        raise
    except HammerheadError as exc:
        log.warning("hammerhead.download.failed", activity_id=activity.id, error=str(exc))
        outcome.failures.append((activity.id, str(exc)))
        return

    try:
        result = import_ride(
            parse(data),
            session,
            name=activity.name or None,
            source=SOURCE,
            external_id=activity.id,
        )
    except (FitParseError, ValueError) as exc:
        log.warning("hammerhead.import.failed", activity_id=activity.id, error=str(exc))
        outcome.failures.append((activity.id, str(exc)))
        return

    if result.created:
        outcome.imported += 1
        log.info("hammerhead.import", activity_id=activity.id, ride_id=result.ride.id)
        return

    # The hash matched a ride that is already here — almost always one the
    # user uploaded by hand before connecting the API. Claim it for this
    # source so the next poll filters it out instead of fetching it again.
    _adopt(session, result.ride, activity.id)
    outcome.skipped += 1


def _adopt(session: Session, ride: Ride, activity_id: str) -> None:
    """Record which activity an already-imported ride corresponds to.

    ``source`` is left alone: the ride really did arrive as a file, and
    rewriting history to say otherwise would make the provenance column a lie.
    Only ``external_id`` matters for deduplication.
    """
    if ride.external_id == activity_id:
        return
    ride.external_id = activity_id
    session.add(ride)
    session.commit()
    log.info("hammerhead.adopt", ride_id=ride.id, activity_id=activity_id)


def _known_external_ids(session: Session) -> set[str]:
    """Every activity id already accounted for, in one query.

    Not filtered by ``source``: a ride adopted by :func:`_adopt` keeps
    ``source="file"`` and must still count as held.
    """
    rows = session.exec(select(Ride.external_id).where(col(Ride.external_id).is_not(None))).all()
    return {row for row in rows if row}


def _list_all(client: HammerheadClient, since: date | None) -> list[ActivitySummary]:
    """Walk every page of the activity list.

    The whole list is collected before anything is downloaded, because the
    cursor decision needs the newest ``createdAt`` across all pages — and
    because pagination while inserting rows is a way to read a page twice.
    """
    collected: list[ActivitySummary] = []
    page = 1
    while page <= MAX_PAGES:
        body = client.list_activities(page=page, start_date=since)
        collected.extend(body.activities)
        if page >= body.total_pages or not body.activities:
            break
        page += 1
    else:
        log.warning("hammerhead.list.truncated", pages=MAX_PAGES, listed=len(collected))
    return collected


def summarize_failures(failures: Sequence[tuple[str, str]], limit: int = 3) -> str:
    """One line naming the first few failures, for the status card."""
    if not failures:
        return ""
    shown = ", ".join(f"{aid}: {msg}" for aid, msg in failures[:limit])
    rest = len(failures) - limit
    return f"{shown} (+{rest} more)" if rest > 0 else shown


def cursor_for(backfill_start: date | None, cursor_date: date | None) -> date | None:
    """Which ``startDate`` the next poll should ask for.

    The cursor wins once it exists; before that it is the user's chosen
    backfill date; and ``None`` means "everything", which only happens if the
    user cleared the date deliberately.
    """
    return cursor_date or backfill_start


def poll_window_label(since: date | None, now: datetime) -> str:
    """Human text for the status card, so the card can say what it will do."""
    if since is None:
        return "the whole account"
    days = max((now.date() - since).days, 0)
    return f"since {since.isoformat()} ({days} days)"
