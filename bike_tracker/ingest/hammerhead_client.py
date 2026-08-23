"""Hammerhead REST client (brief §3.2, masterplan §2.6.2).

Written against ``https://api.hammerhead.io/v1/docs/openapi.yml``. The docs
page is Redoc and renders nothing to a fetcher; the YAML behind it is the
readable source and is what every endpoint and field name here comes from.

No Reflex, no models, no session — httpx and dataclasses only. That is what
lets the tests drive the real client against a fake server without an app or
a database, which matters because *every* failure this module has to survive
is a network condition that cannot be provoked from the UI.

Three endpoints are used, all scope ``activity:read``:

* ``GET /activities`` — paginated summaries, filtered by ``startDate``
* ``GET /activities/{id}/file`` — the FIT bytes, ``application/vnd.ant.fit``
* ``POST /oauth/token`` — code exchange and refresh, form-encoded

``GET /activities/{id}`` is deliberately unused: it adds ``activityType``,
``polyline`` and ``updatedAt``, none of which the FIT file does not already
carry better.
"""

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..utils.logging import get_logger

log = get_logger(__name__)

# The two hosts are genuinely different: auth lives outside /api.
DEFAULT_AUTH_BASE = "https://api.hammerhead.io/v1/auth"
DEFAULT_API_BASE = "https://api.hammerhead.io/v1/api"

# The only scope this app needs. Route and workout push are out of scope, and
# `metrics` is read-only nowhere in the spec (only `metrics:write` exists), so
# asking for more would be asking the user to grant what we never use.
SCOPES = "activity:read"

# The spec caps perPage at 100.
MAX_PER_PAGE = 100

# Refresh this far before the token actually expires. A poll that starts at
# T-30s and takes a minute would otherwise 401 halfway through.
REFRESH_MARGIN = timedelta(minutes=5)

REQUEST_TIMEOUT_S = 30.0
# A FIT file is a megabyte or two; the list endpoint is small. One generous
# timeout for the download, a short one for everything else.
DOWNLOAD_TIMEOUT_S = 120.0


def _utc_now() -> datetime:
    """Naive UTC. Matches the timestamp columns; `datetime.utcnow` is deprecated."""
    return datetime.now(UTC).replace(tzinfo=None)


class HammerheadError(Exception):
    """Base for anything this client raises."""


class HammerheadAuthError(HammerheadError):
    """The connection is dead and the user must re-authorise.

    A 401, or an ``invalid_grant`` from the token endpoint. Distinct from
    :class:`HammerheadApiError` because the failure policies differ: this one
    switches the poller off, the other one waits for the next tick. Collapsing
    the two either hammers the API forever after a revoked token, or disables
    the poller because Hammerhead had a bad minute.
    """


class HammerheadApiError(HammerheadError):
    """A transient failure — 5xx, a timeout, a malformed body. Retry later."""


class HammerheadNotConfigured(HammerheadError):
    """No client id/secret in the environment."""


@dataclass(frozen=True)
class ClientCredentials:
    """The app's own identity, from the environment and nowhere else.

    These are the same on every run and are not the user's to type, so there
    is no form field for them anywhere in the UI. ``from_env`` returns
    ``None`` rather than raising when they are absent, so Settings can render
    a "not configured" card instead of a traceback.
    """

    client_id: str
    client_secret: str
    redirect_uri: str
    auth_base: str = DEFAULT_AUTH_BASE
    api_base: str = DEFAULT_API_BASE

    @classmethod
    def from_env(cls) -> "ClientCredentials | None":
        client_id = os.environ.get("HAMMERHEAD_CLIENT_ID", "").strip()
        client_secret = os.environ.get("HAMMERHEAD_CLIENT_SECRET", "").strip()
        redirect_uri = os.environ.get("HAMMERHEAD_REDIRECT_URI", "").strip()
        if not (client_id and client_secret and redirect_uri):
            return None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            # The bases are overridable so the tests can point the real client
            # at a fake server. Nothing else needs them.
            auth_base=os.environ.get("HAMMERHEAD_AUTH_BASE", DEFAULT_AUTH_BASE).rstrip("/"),
            api_base=os.environ.get("HAMMERHEAD_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        )


@dataclass
class TokenSet:
    """What the token endpoint returns, plus an absolute expiry.

    ``expires_in`` is relative and useless the moment it is stored, so it is
    resolved to a wall-clock time here, once, at the only place that knows
    when the response arrived.
    """

    access_token: str
    refresh_token: str
    expires_at: datetime
    user_id: str | None = None
    # What the server says was granted. The spec's token response does not
    # list a `scope` field, but most OAuth servers send one — and the consent
    # screen lets the user approve a subset of what was requested, so the
    # request is not evidence of the grant. `None` when the server is silent.
    scope: str | None = None

    def expiring(self, now: datetime) -> bool:
        return now + REFRESH_MARGIN >= self.expires_at


@dataclass(frozen=True)
class ActivitySummary:
    """One row of ``GET /activities``.

    ``created_at`` is the string the API sent, kept verbatim alongside the
    parsed date: the FIT file is the authority on when the ride happened, and
    this value is only ever used to move the poll cursor.
    """

    id: str
    name: str
    created_at: datetime
    duration_s: float | None = None
    distance: float | None = None


@dataclass
class ActivityPage:
    activities: list[ActivitySummary] = field(default_factory=list)
    current_page: int = 1
    total_pages: int = 1
    total_items: int = 0


# ---------------------------------------------------------------------------
# The authorization-code dance, as pure functions
# ---------------------------------------------------------------------------


def new_state() -> str:
    """An opaque value echoed back through the redirect, to bind the two."""
    return secrets.token_urlsafe(24)


def authorize_url(creds: ClientCredentials, state: str) -> str:
    """Where the user's browser goes to grant consent."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": creds.client_id,
            "redirect_uri": creds.redirect_uri,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{creds.auth_base}/oauth/authorize?{query}"


@dataclass(frozen=True)
class RedirectResult:
    code: str
    state: str | None


def parse_redirect(text: str) -> RedirectResult:
    """Accept either the bare code or the whole URL the browser landed on.

    Both are supported because it is not known whether Hammerhead's redirect
    URL field accepts a LAN address. If it does, the callback page reads the
    query itself and this never runs on a URL; if it does not, the user copies
    the address bar, and asking them to also extract one parameter from it by
    hand is how a working connection turns into a support question.

    Raises:
        HammerheadAuthError: the consent screen was denied, or there is no
            code in the text at all.
    """
    text = text.strip()
    if not text:
        raise HammerheadAuthError("Nothing pasted.")

    if "?" in text or text.startswith("http"):
        params = parse_qs(urlparse(text).query)
        error = params.get("error", [""])[0]
        if error:
            description = params.get("error_description", [""])[0]
            raise HammerheadAuthError(f"Hammerhead returned '{error}'. {description}".strip())
        code = params.get("code", [""])[0]
        state = params.get("state", [None])[0]
        if not code:
            raise HammerheadAuthError("That URL carries no 'code' parameter.")
        return RedirectResult(code=code, state=state)

    # A bare code. Whitespace is the only thing worth guarding against; the
    # format is opaque and must not be validated against a guess.
    return RedirectResult(code=text, state=None)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class HammerheadClient:
    """Authenticated access to the activity endpoints.

    Args:
        creds: The app's client id/secret and redirect URI.
        tokens: The current token set, or ``None`` before the first exchange.
        on_token_refresh: Called with the new :class:`TokenSet` whenever one
            is minted. This is how the rotated refresh token reaches the
            database. Dropping it leaves a connection that works until the
            access token expires and then fails with nothing to explain why.
        now: Injectable clock, so expiry can be tested without sleeping.
    """

    def __init__(
        self,
        creds: ClientCredentials,
        tokens: TokenSet | None = None,
        *,
        on_token_refresh: Callable[[TokenSet], None] | None = None,
        now: Callable[[], datetime] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.creds = creds
        self.tokens = tokens
        self._on_refresh = on_token_refresh
        self._now = now or _utc_now
        self._http = client or httpx.Client(timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
        self._owns_http = client is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "HammerheadClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- token endpoint ---------------------------------------------------

    def exchange_code(self, code: str) -> TokenSet:
        """Trade an authorization code for a token set."""
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.creds.redirect_uri,
            }
        )

    def refresh(self) -> TokenSet:
        """Mint a new access token from the refresh token."""
        if self.tokens is None:
            raise HammerheadAuthError("Not connected.")
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": self.tokens.refresh_token}
        )

    def _token_request(self, extra: dict[str, str]) -> TokenSet:
        payload = {
            "client_id": self.creds.client_id,
            "client_secret": self.creds.client_secret,
            **extra,
        }
        try:
            response = self._http.post(f"{self.creds.auth_base}/oauth/token", data=payload)
        except httpx.HTTPError as exc:
            raise HammerheadApiError(f"Token request failed: {exc}") from exc

        if response.status_code in (400, 401, 403):
            # 400 is where OAuth puts `invalid_grant`, which means the code or
            # refresh token is spent — a re-authorisation, not a retry.
            raise HammerheadAuthError(_describe(response))
        if response.status_code >= 500:
            raise HammerheadApiError(_describe(response))
        if response.status_code != 200:
            raise HammerheadApiError(_describe(response))

        try:
            body = response.json()
            tokens = TokenSet(
                access_token=body["access_token"],
                refresh_token=body["refresh_token"],
                expires_at=self._now() + timedelta(seconds=int(body.get("expires_in", 3600))),
                user_id=str(body["user_id"]) if body.get("user_id") is not None else None,
                scope=str(body["scope"]) if body.get("scope") else None,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise HammerheadApiError(f"Unreadable token response: {exc}") from exc

        self.tokens = tokens
        if self._on_refresh is not None:
            self._on_refresh(tokens)
        log.info("hammerhead.token", user_id=tokens.user_id, expires_at=tokens.expires_at)
        return tokens

    def deauthorize(self) -> None:
        """Revoke the link at Hammerhead's end.

        The spec says this "removes all of their imported routes and
        workouts" — meaning routes and workouts pushed *by this client*, of
        which there are none. It does not touch activities. Failure is logged
        and swallowed: the local tokens are being dropped either way, and a
        disconnect that cannot complete because Hammerhead is down would leave
        the user stuck connected to nothing.
        """
        if self.tokens is None:
            return
        try:
            self._http.post(
                f"{self.creds.auth_base}/oauth/deauthorize",
                data={
                    "client_id": self.creds.client_id,
                    "client_secret": self.creds.client_secret,
                    "token": self.tokens.access_token,
                },
            )
        except httpx.HTTPError as exc:
            log.warning("hammerhead.deauthorize.failed", error=str(exc))

    # --- activity endpoints ----------------------------------------------

    def list_activities(
        self,
        *,
        page: int = 1,
        per_page: int = MAX_PER_PAGE,
        start_date: date | None = None,
    ) -> ActivityPage:
        params: dict[str, str | int] = {"page": page, "perPage": min(per_page, MAX_PER_PAGE)}
        if start_date is not None:
            params["startDate"] = start_date.isoformat()

        body = self._get_json("/activities", params=params)
        try:
            activities = [_to_summary(row) for row in body.get("data", [])]
        except (KeyError, TypeError, ValueError) as exc:
            raise HammerheadApiError(f"Unreadable activity list: {exc}") from exc

        return ActivityPage(
            activities=activities,
            current_page=int(body.get("currentPage", page)),
            total_pages=int(body.get("totalPages", 1)),
            total_items=int(body.get("totalItems", len(activities))),
        )

    def download_fit(self, activity_id: str) -> bytes:
        """The raw FIT bytes for one activity.

        Returned rather than written: FIT files are never persisted (§1.3),
        and ``ingest.fit.parse`` takes bytes.
        """
        response = self._request(
            "GET", f"/activities/{activity_id}/file", timeout=DOWNLOAD_TIMEOUT_S
        )
        if not response.content:
            raise HammerheadApiError(f"Activity {activity_id} returned an empty file.")
        return response.content

    # --- plumbing ---------------------------------------------------------

    def _get_json(self, path: str, *, params: dict[str, str | int]) -> dict:
        response = self._request("GET", path, params=params)
        try:
            body = response.json()
        except ValueError as exc:
            raise HammerheadApiError(f"{path} did not return JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise HammerheadApiError(f"{path} returned {type(body).__name__}, expected an object.")
        return body

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        self._ensure_fresh_token()
        if self.tokens is None:
            raise HammerheadAuthError("Not connected.")

        try:
            response = self._http.request(
                method,
                f"{self.creds.api_base}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self.tokens.access_token}"},
                timeout=timeout or REQUEST_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise HammerheadApiError(f"{method} {path} failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise HammerheadAuthError(_describe(response))
        if response.status_code != 200:
            raise HammerheadApiError(_describe(response))
        return response

    def _ensure_fresh_token(self) -> None:
        if self.tokens is not None and self.tokens.expiring(self._now()):
            log.info("hammerhead.token.refresh", expires_at=self.tokens.expires_at)
            self.refresh()


def _to_summary(row: dict) -> ActivitySummary:
    return ActivitySummary(
        id=str(row["id"]),
        name=str(row.get("name") or "").strip(),
        created_at=_parse_timestamp(str(row["createdAt"])),
        duration_s=_maybe_float(row.get("duration")),
        distance=_maybe_float(row.get("distance")),
    )


def _parse_timestamp(text: str) -> datetime:
    """ISO 8601 with a trailing Z, normalised to naive UTC.

    Naive because every other timestamp in this schema is: SQLite drops tzinfo
    on write, so mixing the two only surfaces later as a comparison that
    raises. An offset-less value is taken as UTC, which is what the spec's
    examples are.
    """
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _maybe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _describe(response: httpx.Response) -> str:
    """A short, loggable summary of a failed response.

    Truncated: an HTML error page from a proxy is not worth a megabyte in the
    `last_poll_error` column or on screen.
    """
    body = (response.text or "").strip().replace("\n", " ")
    if len(body) > 200:
        body = body[:200] + "…"
    return f"HTTP {response.status_code}: {body}" if body else f"HTTP {response.status_code}"
