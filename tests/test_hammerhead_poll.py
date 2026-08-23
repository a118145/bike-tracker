"""What the poller fetches, and — more importantly — what it does not.

Every assertion here that matters is about the *requests made*, not the rides
created. The two dedup bugs this guards against both leave the ride table
looking perfect:

* re-downloading an activity that is already held costs a megabyte a poll and
  changes nothing observable in the database;
* a ride uploaded by hand before the API was connected is downloaded on every
  poll for ever, and the only symptom is traffic.

So the fake server keeps a request log and the tests read it.
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
import reflex as rx
from sqlmodel import select

from bike_tracker.ingest.fit import parse
from bike_tracker.ingest.hammerhead_client import (
    ClientCredentials,
    HammerheadAuthError,
    HammerheadClient,
)
from bike_tracker.ingest.importer import import_ride
from bike_tracker.ingest.sources.hammerhead import poll
from bike_tracker.models import Ride
from tests.conftest import EVENING_FIT, MORNING_FIT
from tests.fake_hammerhead import VALID_CODE, FakeActivity, FakeHammerhead, RunningFake

BASE = datetime(2026, 6, 1, 8, 0)


@pytest.fixture
def fake() -> FakeHammerhead:
    return FakeHammerhead(
        activities=[
            FakeActivity("act-morning", "Morning Ride", BASE, MORNING_FIT.read_bytes()),
            FakeActivity(
                "act-evening", "Evening Ride", BASE + timedelta(days=1), EVENING_FIT.read_bytes()
            ),
        ]
    )


@pytest.fixture
def server(fake: FakeHammerhead) -> Iterator[RunningFake]:
    with RunningFake(fake) as running:
        yield running


@pytest.fixture
def client(server: RunningFake) -> HammerheadClient:
    creds = ClientCredentials(
        client_id="cid",
        client_secret="secret",
        redirect_uri="http://localhost:3000/settings/hammerhead/callback",
        auth_base=server.auth_base,
        api_base=server.api_base,
    )
    built = HammerheadClient(creds)
    built.exchange_code(VALID_CODE)
    return built


def _ride_names(db_url: str) -> list[str]:
    with rx.session(url=db_url) as session:
        return sorted(r.name for r in session.exec(select(Ride)).all())


def _downloads(server: RunningFake) -> list[str]:
    return server.paths("/file")


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_first_poll_imports_every_activity(
    db_url: str, client: HammerheadClient, server: RunningFake
) -> None:
    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None)

    assert outcome.imported == 2
    assert outcome.failed == 0
    assert _ride_names(db_url) == ["Evening Ride", "Morning Ride"]


def test_the_activity_name_becomes_the_ride_name(db_url: str, client: HammerheadClient) -> None:
    """The API has a real name field. The file source has to recover one from
    the filename, and this must not fall back to that."""
    with rx.session(url=db_url) as session:
        poll(session, client, since=None)
    assert "Evening Ride" in _ride_names(db_url)


def test_the_source_and_external_id_are_recorded(db_url: str, client: HammerheadClient) -> None:
    with rx.session(url=db_url) as session:
        poll(session, client, since=None)
        rides = session.exec(select(Ride)).all()

    assert {r.source for r in rides} == {"hammerhead"}
    assert {r.external_id for r in rides} == {"act-morning", "act-evening"}


# ---------------------------------------------------------------------------
# Deduplication — the request log is the assertion
# ---------------------------------------------------------------------------


def test_a_second_poll_downloads_nothing(
    db_url: str, client: HammerheadClient, server: RunningFake
) -> None:
    """The bug this catches is invisible in the ride table.

    `import_ride` refuses a duplicate by fit_hash, so the ride count is right
    either way. Filtering on the activity id first is what turns a poll over
    200 known rides into one request instead of 201.
    """
    with rx.session(url=db_url) as session:
        poll(session, client, since=None)
    first_round = len(_downloads(server))

    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None)

    assert len(_downloads(server)) == first_round, "a known activity was fetched again"
    assert outcome.imported == 0
    assert outcome.skipped == 2


def test_a_manually_uploaded_ride_is_adopted_and_never_refetched(
    db_url: str, client: HammerheadClient, server: RunningFake
) -> None:
    """The subtler half of the same bug.

    A ride imported from a file has no external_id, so it is never in the
    known set. Without adoption it is downloaded on *every* poll for ever —
    the hash check only stops it after the bytes are on the wire.
    """
    with rx.session(url=db_url) as session:
        import_ride(parse(MORNING_FIT.read_bytes()), session, name="Morning Ride", source="file")

    with rx.session(url=db_url) as session:
        first = poll(session, client, since=None)
    assert "/v1/api/activities/act-morning/file" in _downloads(server)
    assert first.imported == 1, "only the evening ride is new"

    with rx.session(url=db_url) as session:
        ride = session.exec(select(Ride).where(Ride.name == "Morning Ride")).one()
        assert ride.external_id == "act-morning"
        assert ride.source == "file", "provenance is not rewritten"

    before = len(_downloads(server))
    with rx.session(url=db_url) as session:
        poll(session, client, since=None)
    assert len(_downloads(server)) == before


# ---------------------------------------------------------------------------
# The cursor
# ---------------------------------------------------------------------------


def test_the_cursor_lands_behind_the_newest_activity(db_url: str, client: HammerheadClient) -> None:
    """A Karoo uploads when it finds wifi, so an activity can appear with a
    createdAt behind the watermark. The overlap is what still catches it."""
    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None)

    # Written out rather than derived from CURSOR_OVERLAP_DAYS: an expectation
    # computed from the constant it pins passes at every value, including
    # zero, which is the regression that matters.
    newest = (BASE + timedelta(days=1)).date()
    assert outcome.next_cursor == date(2026, 5, 30)
    assert outcome.next_cursor is not None and outcome.next_cursor < newest


def test_a_truncated_run_leaves_the_cursor_alone(db_url: str, client: HammerheadClient) -> None:
    """Advancing it here would skip the rides the cap did not reach, silently
    and permanently."""
    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None, max_downloads=1)

    assert outcome.truncated
    assert outcome.imported == 1
    assert outcome.next_cursor is None, "the cursor must not move past unfetched rides"


def test_the_rest_arrives_on_the_next_poll(db_url: str, client: HammerheadClient) -> None:
    with rx.session(url=db_url) as session:
        poll(session, client, since=None, max_downloads=1)
    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None, max_downloads=1)

    assert outcome.imported == 1
    assert len(_ride_names(db_url)) == 2


def test_start_date_limits_what_is_listed(db_url: str, client: HammerheadClient) -> None:
    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=date(2026, 6, 2))

    assert outcome.imported == 1
    assert _ride_names(db_url) == ["Evening Ride"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def test_one_failed_download_costs_that_ride_and_not_the_run(
    db_url: str, client: HammerheadClient, fake: FakeHammerhead
) -> None:
    fake.fail_download_ids = {"act-morning"}

    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None)

    assert outcome.imported == 1
    assert [aid for aid, _ in outcome.failures] == ["act-morning"]
    assert _ride_names(db_url) == ["Evening Ride"]


def test_a_corrupt_file_costs_that_ride_and_not_the_run(
    db_url: str, client: HammerheadClient, fake: FakeHammerhead
) -> None:
    """A truncated FIT file is a real thing, and one of forty must not abort
    the other thirty-nine."""
    fake.activities[0] = FakeActivity("act-morning", "Morning Ride", BASE, b"not a fit file at all")

    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None)

    assert outcome.imported == 1
    assert outcome.failed == 1


def test_a_failed_activity_is_retried_on_the_next_poll(
    db_url: str, client: HammerheadClient, fake: FakeHammerhead
) -> None:
    """It was never imported, so it is not in the known set — the retry falls
    out of the dedup rule rather than needing a retry queue."""
    fake.fail_download_ids = {"act-morning"}
    with rx.session(url=db_url) as session:
        poll(session, client, since=None)

    fake.fail_download_ids = set()
    with rx.session(url=db_url) as session:
        outcome = poll(session, client, since=None)

    assert outcome.imported == 1
    assert _ride_names(db_url) == ["Evening Ride", "Morning Ride"]


def test_an_auth_failure_listing_aborts_the_run(
    db_url: str, client: HammerheadClient, fake: FakeHammerhead
) -> None:
    fake.unauthorized = True

    with rx.session(url=db_url) as session, pytest.raises(HammerheadAuthError):
        poll(session, client, since=None)


def test_an_auth_failure_mid_download_aborts_the_rest(
    db_url: str, client: HammerheadClient, fake: FakeHammerhead, server: RunningFake
) -> None:
    """The token can be revoked between the list and the downloads.

    Every remaining download would then fail identically, so recording forty
    copies of one message instead of stopping makes the status line useless —
    and it is the caller's job to switch the poller off, which it only does
    for an exception. The 401 must reach a *download*, not the list: the list
    is requested first, so `unauthorized` alone never exercises this path.
    """
    fake.unauthorized_download_ids = {"act-morning"}

    with rx.session(url=db_url) as session, pytest.raises(HammerheadAuthError):
        poll(session, client, since=None)

    # It gave up rather than working through the rest of the list.
    assert _downloads(server) == ["/v1/api/activities/act-morning/file"]


def test_a_listing_failure_raises_rather_than_reporting_success(
    db_url: str, client: HammerheadClient, fake: FakeHammerhead
) -> None:
    """If the list cannot be read it is not known what is missing, so
    reporting "0 imported" would look like a quiet, successful poll."""
    from bike_tracker.ingest.hammerhead_client import HammerheadApiError

    fake.fail_list_times = 1
    with rx.session(url=db_url) as session, pytest.raises(HammerheadApiError):
        poll(session, client, since=None)
