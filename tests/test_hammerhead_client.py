"""The Hammerhead client against a server, not a mock.

Everything here is a network condition — an expired token, a rotated refresh
token, a 401, a 503, a paginated list, a binary body. A canned-object mock
would pass on all of them while the plumbing was wrong, so these run the real
``httpx`` client against ``tests/fake_hammerhead``, which is written from the
published OpenAPI spec.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

import pytest

from bike_tracker.ingest.hammerhead_client import (
    ClientCredentials,
    HammerheadApiError,
    HammerheadAuthError,
    HammerheadClient,
    TokenSet,
    authorize_url,
    parse_redirect,
)
from tests.conftest import EVENING_FIT, MORNING_FIT
from tests.fake_hammerhead import VALID_CODE, FakeActivity, FakeHammerhead, RunningFake

BASE = datetime(2026, 6, 1, 8, 0)


@pytest.fixture
def fake() -> FakeHammerhead:
    return FakeHammerhead(
        activities=[
            FakeActivity("1000.activity.a", "Morning Ride", BASE, MORNING_FIT.read_bytes()),
            FakeActivity(
                "1000.activity.b",
                "Evening Ride",
                BASE + timedelta(days=1),
                EVENING_FIT.read_bytes(),
            ),
        ]
    )


@pytest.fixture
def server(fake: FakeHammerhead) -> Iterator[RunningFake]:
    with RunningFake(fake) as running:
        yield running


def _creds(server: RunningFake) -> ClientCredentials:
    return ClientCredentials(
        client_id="cid",
        client_secret="secret",
        redirect_uri="http://localhost:3000/settings/hammerhead/callback",
        auth_base=server.auth_base,
        api_base=server.api_base,
    )


def _connected(
    server: RunningFake, *, now: Callable[[], datetime] | None = None
) -> HammerheadClient:
    client = HammerheadClient(_creds(server), now=now)
    client.exchange_code(VALID_CODE)
    return client


# ---------------------------------------------------------------------------
# The authorization dance
# ---------------------------------------------------------------------------


def test_the_authorize_url_carries_every_parameter_the_spec_requires() -> None:
    creds = ClientCredentials("cid", "secret", "http://host/cb")
    url = authorize_url(creds, "st8")
    for fragment in ("response_type=code", "client_id=cid", "scope=activity", "state=st8"):
        assert fragment in url


def test_parse_redirect_accepts_a_bare_code_and_a_whole_url() -> None:
    assert parse_redirect("  abc123 ").code == "abc123"

    parsed = parse_redirect("http://localhost:3000/cb?code=abc123&state=st8")
    assert (parsed.code, parsed.state) == ("abc123", "st8")


def test_parse_redirect_reports_a_denied_consent_screen() -> None:
    """The user clicking Deny is the most likely non-success, and it arrives
    as a 200 page, not an error status."""
    with pytest.raises(HammerheadAuthError, match="access_denied"):
        parse_redirect("http://localhost:3000/cb?error=access_denied&state=st8")


def test_a_wrong_code_is_an_auth_error_not_a_transient_one(server: RunningFake) -> None:
    """`invalid_grant` arrives as HTTP 400. Classifying it as transient would
    make the poller retry a spent code every interval for ever."""
    client = HammerheadClient(_creds(server))
    with pytest.raises(HammerheadAuthError):
        client.exchange_code("not-the-code")


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_a_rotated_refresh_token_is_handed_to_the_callback(server: RunningFake) -> None:
    """The bug this guards is invisible for 14 hours.

    Hammerhead may return a new refresh token on every exchange. A client that
    mints one and does not persist it keeps working until the access token
    expires, and then fails with a message that explains nothing.
    """
    seen: list[TokenSet] = []
    client = HammerheadClient(_creds(server), on_token_refresh=seen.append)
    first = client.exchange_code(VALID_CODE)

    client.refresh()

    assert len(seen) == 2
    assert seen[1].refresh_token != first.refresh_token
    assert seen[1].access_token != first.access_token


def test_a_token_close_to_expiry_is_refreshed_before_the_call(server: RunningFake) -> None:
    clock = [datetime(2026, 6, 1, 12, 0)]
    client = _connected(server, now=lambda: clock[0])
    assert client.tokens is not None

    # Four minutes left, inside the five-minute margin.
    clock[0] = client.tokens.expires_at - timedelta(minutes=4)
    client.list_activities()

    assert server.paths("/oauth/token") == ["/v1/auth/oauth/token"] * 2


def test_a_token_with_time_left_is_not_refreshed(server: RunningFake) -> None:
    """The other half of the same guard: refreshing on every call would work,
    and would triple the request count for nothing."""
    clock = [datetime(2026, 6, 1, 12, 0)]
    client = _connected(server, now=lambda: clock[0])

    client.list_activities()

    assert server.paths("/oauth/token") == ["/v1/auth/oauth/token"]


def test_expiry_is_absolute_not_relative(server: RunningFake) -> None:
    """`expires_in` is useless the moment it is stored."""
    now = datetime(2026, 6, 1, 12, 0)
    client = _connected(server, now=lambda: now)
    assert client.tokens is not None
    assert client.tokens.expires_at > now
    assert client.tokens.expires_at - now == timedelta(seconds=52000)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


def test_the_list_walks_every_page(server: RunningFake, fake: FakeHammerhead) -> None:
    fake.per_page = 1
    client = _connected(server)

    first = client.list_activities()

    assert first.total_pages == 2
    assert len(first.activities) == 1


def test_created_at_parses_the_spec_s_z_suffixed_timestamp(server: RunningFake) -> None:
    client = _connected(server)
    activity = client.list_activities().activities[0]
    assert activity.created_at == BASE
    assert activity.created_at.tzinfo is None, "naive UTC, like every column in the schema"


def test_start_date_reaches_the_server_as_an_iso_date(
    server: RunningFake, fake: FakeHammerhead
) -> None:
    client = _connected(server)
    page = client.list_activities(start_date=date(2026, 6, 2))
    assert [a.id for a in page.activities] == ["1000.activity.b"]


def test_the_downloaded_bytes_are_the_file(server: RunningFake) -> None:
    client = _connected(server)
    assert client.download_fit("1000.activity.a") == MORNING_FIT.read_bytes()


# ---------------------------------------------------------------------------
# Failures, and the distinction the whole failure policy rests on
# ---------------------------------------------------------------------------


def test_a_401_is_an_auth_error(server: RunningFake, fake: FakeHammerhead) -> None:
    client = _connected(server)
    fake.unauthorized = True
    with pytest.raises(HammerheadAuthError):
        client.list_activities()


def test_a_5xx_is_a_transient_error(server: RunningFake, fake: FakeHammerhead) -> None:
    """Not an auth error. Conflating the two disables the poller because
    Hammerhead had a bad minute."""
    client = _connected(server)
    fake.fail_list_times = 1
    with pytest.raises(HammerheadApiError):
        client.list_activities()


def test_an_unreachable_host_is_transient_not_auth() -> None:
    creds = ClientCredentials(
        "cid",
        "secret",
        "http://host/cb",
        auth_base="http://127.0.0.1:1/v1/auth",
        api_base="http://127.0.0.1:1/v1/api",
    )
    tokens = TokenSet("a", "r", datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1))
    with HammerheadClient(creds, tokens) as client, pytest.raises(HammerheadApiError):
        client.list_activities()


# ---------------------------------------------------------------------------
# Scope: what was granted, not what was asked for
# ---------------------------------------------------------------------------


def test_the_granted_scope_is_read_from_the_response(
    server: RunningFake, fake: FakeHammerhead
) -> None:
    """The consent screen lets the user approve a *subset* of what was asked.

    Recording the request instead would let the Settings card claim
    `activity:read` while every call 403s.
    """
    fake.grant_scope = "activity:read route:read"
    client = HammerheadClient(_creds(server))

    tokens = client.exchange_code(VALID_CODE)

    assert tokens.scope == "activity:read route:read"


def test_a_silent_server_leaves_the_scope_unknown(
    server: RunningFake, fake: FakeHammerhead
) -> None:
    """The spec's token response lists no `scope` field, so it may be absent.
    `None` lets the caller fall back rather than inventing a value here."""
    fake.grant_scope = None
    client = HammerheadClient(_creds(server))

    assert client.exchange_code(VALID_CODE).scope is None
