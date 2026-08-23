"""Settings → Hammerhead section (masterplan §2.6.2).

Everything real lives in ``ingest/``: this state reads the account row,
renders it as strings, and calls the sync layer. It deliberately owns no
protocol knowledge — the same poll must happen whether it was started by this
page or by the scheduler, and that is only true if neither of them implements
it.

**No secret is ever a state var.** The client id and secret come from the
environment (see ``ingest/hammerhead_client.ClientCredentials``) and the
access and refresh tokens stay in the database. What the page shows is
whether they exist, never what they are — a state var is JSON on the wire and
in every browser devtools panel.
"""

import asyncio
from datetime import date, datetime

import reflex as rx

from ..components.toast import notify_error, notify_info, notify_success
from ..ingest import hammerhead_sync, scheduler
from ..ingest.hammerhead_client import (
    HammerheadAuthError,
    authorize_url,
    new_state,
    parse_redirect,
)
from ..models import HammerheadAccount, get_account, now_utc
from ..navigation import routes
from ..utils.formatting import EMPTY, format_datetime
from ..utils.logging import get_logger

log = get_logger(__name__).bind(state="HammerheadState")

# What the "not configured" card tells the user to set.
ENV_VARS = ["HAMMERHEAD_CLIENT_ID", "HAMMERHEAD_CLIENT_SECRET", "HAMMERHEAD_REDIRECT_URI"]


class HammerheadState(rx.State):
    # --- what exists ------------------------------------------------------
    configured: bool = False
    connected: bool = False
    redirect_uri: str = ""

    # --- connection readout ----------------------------------------------
    account_user_id: str = EMPTY
    scopes: str = EMPTY
    connected_since: str = EMPTY
    token_expires: str = EMPTY

    # --- settings ---------------------------------------------------------
    enabled: bool = False
    interval_min: int = 30
    backfill_start: str = ""
    next_start_date: str = ""

    # --- last run ---------------------------------------------------------
    last_poll: str = EMPTY
    last_poll_ok: bool = True
    last_poll_summary: str = ""
    last_poll_error: str = ""

    # --- the authorize/paste exchange -------------------------------------
    authorize_link: str = ""
    paste_value: str = ""
    exchanging: bool = False
    polling: bool = False

    @rx.var
    def env_vars(self) -> list[str]:
        return ENV_VARS

    @rx.var
    def has_last_poll(self) -> bool:
        return self.last_poll != EMPTY

    @rx.var
    def poll_button_label(self) -> str:
        return "Polling…" if self.polling else "Poll now"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @rx.event
    def load(self):
        """Read the account row and the environment into display strings."""
        creds = hammerhead_sync.credentials()
        self.configured = creds is not None
        self.redirect_uri = creds.redirect_uri if creds else ""

        with rx.session() as session:
            account = get_account(session)
            self._apply(account)

        log.info("hammerhead.settings.load", configured=self.configured, connected=self.connected)

    def _apply(self, account: HammerheadAccount) -> None:
        """Account row → the strings the card renders.

        Formatting here rather than in the component because the card is a
        plain read-only layout and every value it shows has a "missing"
        spelling (rule 13).
        """
        self.connected = account.connected
        self.account_user_id = account.user_id or EMPTY
        self.scopes = account.scopes or EMPTY
        self.connected_since = (
            format_datetime(account.connected_at) if account.connected_at else EMPTY
        )
        self.token_expires = _expiry_text(account.token_expires_at)

        self.enabled = account.enabled
        self.interval_min = account.poll_interval_min
        self.backfill_start = account.backfill_start.isoformat() if account.backfill_start else ""
        upcoming = hammerhead_sync.next_start_date(account)
        self.next_start_date = upcoming.isoformat() if upcoming else ""

        self.last_poll = format_datetime(account.last_poll_at) if account.last_poll_at else EMPTY
        self.last_poll_ok = account.last_poll_ok is not False
        self.last_poll_error = account.last_poll_error or ""
        self.last_poll_summary = (
            f"{account.last_poll_imported} imported, "
            f"{account.last_poll_skipped} already held, "
            f"{account.last_poll_failed} failed"
        )

    # ------------------------------------------------------------------
    # Connecting
    # ------------------------------------------------------------------

    @rx.event
    def start_authorize(self):
        """Mint a `state`, store it, and hand back the consent URL.

        The value is persisted rather than kept in this state because the
        browser may come back through the callback *page*, which is a
        different state instance from the one that generated it.
        """
        creds = hammerhead_sync.credentials()
        if creds is None:
            return notify_error("Hammerhead is not configured. See the card for the variables.")

        state_value = new_state()
        with rx.session() as session:
            account = get_account(session)
            account.oauth_state = state_value
            session.add(account)
            session.commit()

        self.authorize_link = authorize_url(creds, state_value)
        log.info("hammerhead.authorize.start")
        return rx.redirect(self.authorize_link, is_external=True)

    @rx.event
    def set_paste_value(self, value: str):
        self.paste_value = value

    @rx.event
    async def submit_code(self):
        """Exchange whatever was pasted — a bare code or the whole URL."""
        # `yield` then `return`, not `return notify_...`: this is an async
        # generator handler, and a generator cannot return a value.
        text = self.paste_value.strip()
        if not text:
            yield notify_error("Paste the code, or the URL you were redirected to.")
            return

        try:
            parsed = parse_redirect(text)
        except HammerheadAuthError as exc:
            yield notify_error(str(exc))
            return

        if not self._state_matches(parsed.state):
            yield notify_error(
                "That response does not match the authorization this app started. "
                "Click Authorize again and use the fresh link."
            )
            return

        self.exchanging = True
        yield

        result = await asyncio.to_thread(hammerhead_sync.exchange_and_store, parsed.code)
        self.paste_value = ""
        self.exchanging = False
        yield HammerheadState.load

        yield notify_success(result.message) if result.ok else notify_error(result.message)

    @rx.event
    async def handle_callback(self):
        """``on_load`` of the OAuth callback page.

        Reads ``code`` and ``state`` straight off the query string, exchanges
        them, and sends the browser to Settings either way — landing on a
        spinner that never resolves is worse than landing on the card with an
        error toast.
        """
        params = self.router.url.query_parameters
        error = params.get("error", "")
        if error:
            description = params.get("error_description", "")
            yield rx.redirect(routes.SETTINGS_ROUTE)
            yield notify_error(f"Hammerhead returned '{error}'. {description}".strip())
            return

        code = params.get("code", "")
        if not code:
            yield rx.redirect(routes.SETTINGS_ROUTE)
            yield notify_error("The callback carried no authorization code.")
            return

        if not self._state_matches(params.get("state")):
            yield rx.redirect(routes.SETTINGS_ROUTE)
            yield notify_error("That response does not match the authorization this app started.")
            return

        result = await asyncio.to_thread(hammerhead_sync.exchange_and_store, code)
        yield rx.redirect(routes.SETTINGS_ROUTE)
        yield notify_success(result.message) if result.ok else notify_error(result.message)

    def _state_matches(self, returned: str | None) -> bool:
        """Compare against the stored value, tolerating a bare code.

        A pasted bare code carries no state at all, which is not an attack —
        it is the flow working as designed when the redirect URI points
        somewhere this app cannot read. Only a *mismatched* state is refused.
        """
        if returned is None:
            return True
        with rx.session() as session:
            expected = get_account(session).oauth_state
        return bool(expected) and returned == expected

    @rx.event
    async def disconnect(self):
        result = await asyncio.to_thread(hammerhead_sync.disconnect)
        scheduler.apply_settings(enabled=False, interval_min=self.interval_min)
        yield HammerheadState.load
        yield notify_info(result.message)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @rx.event
    def set_enabled(self, value: bool):
        """Switch the timer on or off, and reschedule immediately.

        Persisting without rescheduling would leave the flag and the running
        job disagreeing until the next restart, which is the kind of drift
        that gets diagnosed as "the poller is broken".
        """
        if value and not self.connected:
            return notify_error("Connect to Hammerhead first.")

        self.enabled = value
        self._save(enabled=value)
        scheduler.apply_settings(enabled=value, interval_min=self.interval_min)
        return notify_info("Automatic polling on." if value else "Automatic polling off.")

    @rx.event
    def set_interval(self, value: str):
        minutes = _positive_int(value)
        if minutes is None:
            return notify_error("The interval must be a whole number of minutes.")
        self.interval_min = minutes
        self._save(poll_interval_min=minutes)
        scheduler.apply_settings(enabled=self.enabled, interval_min=minutes)
        return notify_info(f"Polling every {minutes} min.")

    @rx.event
    def set_backfill_start(self, value: str):
        """The date the first poll reaches back to.

        Changing it also clears the cursor: the user moving this date back is
        asking for older rides, and a cursor sitting ahead of it would quietly
        refuse them.
        """
        parsed = _parse_date(value)
        if value.strip() and parsed is None:
            return notify_error("Use a date in the form YYYY-MM-DD.")

        self.backfill_start = value.strip()
        with rx.session() as session:
            account = get_account(session)
            account.backfill_start = parsed
            account.cursor_date = None
            session.add(account)
            session.commit()
            self._apply(account)
        return notify_info("Backfill start updated.")

    def _save(self, **fields: object) -> None:
        with rx.session() as session:
            account = get_account(session)
            for key, value in fields.items():
                setattr(account, key, value)
            session.add(account)
            session.commit()

    # ------------------------------------------------------------------
    # Polling by hand
    # ------------------------------------------------------------------

    @rx.event
    async def poll_now(self):
        """Run one poll regardless of the enabled flag.

        Off the event loop: a poll parses FIT files and writes to SQLite, and
        doing that inline freezes every other session in the app.
        """
        self.polling = True
        yield

        result = await asyncio.to_thread(hammerhead_sync.run_poll_once)

        self.polling = False
        yield HammerheadState.load

        if result.ok:
            yield notify_success(f"Poll finished: {result.message}")
        elif result.reauthorize:
            yield notify_error(f"{result.message} Reconnect to continue polling.")
        else:
            yield notify_error(result.message)


def _expiry_text(expires_at: datetime | None) -> str:
    """ "in 13 h" while it is valid, "expired" once it is not.

    A refresh happens automatically on the next call, so an expired access
    token is not an error — it just should not read as a live one.
    """
    if expires_at is None:
        return EMPTY
    remaining = expires_at - now_utc()
    seconds = remaining.total_seconds()
    if seconds <= 0:
        return "expired — refreshes on the next poll"
    hours = int(seconds // 3600)
    return f"in {hours} h" if hours else f"in {int(seconds // 60)} min"


def _positive_int(value: str) -> int | None:
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
