"""The seam between the client and the database.

Only the decisions that live here are worth pinning — the client's protocol
handling and the poller's fetch logic have their own suites. What this module
owns is the connection lifecycle, and one of those decisions is a footgun
guard rather than a feature.
"""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
import reflex as rx

from bike_tracker.ingest import hammerhead_sync
from bike_tracker.ingest.hammerhead_client import ClientCredentials
from bike_tracker.models import DEFAULT_BACKFILL_DAYS, HammerheadAccount, get_account, now_utc
from tests.fake_hammerhead import VALID_CODE, FakeHammerhead, RunningFake


@pytest.fixture
def fake() -> FakeHammerhead:
    return FakeHammerhead()


@pytest.fixture
def server(fake: FakeHammerhead) -> Iterator[RunningFake]:
    with RunningFake(fake) as running:
        yield running


@pytest.fixture
def wired(db_url: str, server: RunningFake, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point both the credentials and `rx.session` at the test fixtures.

    `hammerhead_sync` opens its own sessions rather than taking one, because
    it is called from a scheduler thread as well as from the UI. So the URL
    has to be swapped rather than a session passed in.
    """
    creds = ClientCredentials(
        client_id="cid",
        client_secret="secret",
        redirect_uri="http://localhost:3000/settings/hammerhead/callback",
        auth_base=server.auth_base,
        api_base=server.api_base,
    )
    monkeypatch.setattr(hammerhead_sync, "credentials", lambda: creds)

    original = rx.session
    monkeypatch.setattr(rx, "session", lambda *a, **kw: original(url=db_url))
    yield


def _account(db_url: str) -> HammerheadAccount:
    with rx.session(url=db_url) as session:
        return get_account(session)


def test_connecting_narrows_the_backfill_to_thirty_days(db_url: str, wired: None) -> None:
    """The footgun this exists to disarm.

    `cursor_for` reads an unset `backfill_start` as "the whole account", so a
    freshly connected account whose owner clicks *Poll now* before touching
    the date field would walk several seasons of history.
    """
    result = hammerhead_sync.exchange_and_store(VALID_CODE)
    assert result.ok, result.message

    expected = (now_utc() - timedelta(days=DEFAULT_BACKFILL_DAYS)).date()
    assert _account(db_url).backfill_start == expected


def test_connecting_never_overwrites_a_date_the_user_chose(db_url: str, wired: None) -> None:
    """Disconnect keeps the date, so reconnecting must not re-narrow a window
    that was widened on purpose."""
    chosen = date(2019, 3, 1)
    with rx.session(url=db_url) as session:
        account = get_account(session)
        account.backfill_start = chosen
        session.add(account)
        session.commit()

    hammerhead_sync.exchange_and_store(VALID_CODE)

    assert _account(db_url).backfill_start == chosen


def test_connecting_records_the_granted_scope_not_the_requested_one(
    db_url: str, wired: None, fake: FakeHammerhead
) -> None:
    """Deliberately different from what this app requests.

    Asserting against a grant that happens to equal `SCOPES` proves nothing —
    the test passes whether the code reads the response or echoes the request.
    """
    fake.grant_scope = "activity:read route:read"

    hammerhead_sync.exchange_and_store(VALID_CODE)

    assert _account(db_url).scopes == "activity:read route:read"


def test_a_silent_server_falls_back_to_the_requested_scope(
    db_url: str, wired: None, fake: FakeHammerhead
) -> None:
    """Better than an empty field: it is what was asked for, and the spec
    documents no `scope` in the response."""
    fake.grant_scope = None
    hammerhead_sync.exchange_and_store(VALID_CODE)
    assert _account(db_url).scopes == "activity:read"


def test_a_rejected_code_leaves_the_account_disconnected(db_url: str, wired: None) -> None:
    result = hammerhead_sync.exchange_and_store("not-the-code")

    assert not result.ok
    account = _account(db_url)
    assert not account.connected
    assert account.backfill_start is None, "a failed connect sets nothing"


def test_disconnect_keeps_the_settings_and_drops_the_tokens(db_url: str, wired: None) -> None:
    hammerhead_sync.exchange_and_store(VALID_CODE)
    with rx.session(url=db_url) as session:
        account = get_account(session)
        account.poll_interval_min = 7
        account.cursor_date = date(2026, 5, 1)
        session.add(account)
        session.commit()

    hammerhead_sync.disconnect()

    account = _account(db_url)
    assert not account.connected
    assert account.access_token is None
    assert not account.enabled
    # Reconnecting the same account must not re-import what it already has.
    assert account.poll_interval_min == 7
    assert account.cursor_date == date(2026, 5, 1)
    assert account.backfill_start is not None


def test_polling_without_a_connection_says_so_rather_than_raising(db_url: str, wired: None) -> None:
    result = hammerhead_sync.run_poll_once()
    assert not result.ok
    assert "Not connected" in result.message
