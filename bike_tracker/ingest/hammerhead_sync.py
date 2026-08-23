"""Everything that joins the Hammerhead client to the database.

``hammerhead_client`` knows HTTP and nothing else; ``sources/hammerhead``
knows which activities to fetch and nothing about where the tokens live. This
module is the seam: it reads the account row, builds an authenticated client,
runs the poll, and writes back what happened.

It is called from exactly two places — the scheduler tick and the *Poll now*
button — and both must behave identically, which is why neither of them
contains any of this.
"""

import threading
from dataclasses import dataclass
from datetime import date, timedelta

import reflex as rx
from sqlmodel import Session

from ..models import DEFAULT_BACKFILL_DAYS, HammerheadAccount, get_account, now_utc
from ..utils.logging import get_logger
from .hammerhead_client import (
    SCOPES,
    ClientCredentials,
    HammerheadApiError,
    HammerheadAuthError,
    HammerheadClient,
    HammerheadNotConfigured,
    TokenSet,
)
from .sources.hammerhead import PollOutcome, cursor_for, poll, summarize_failures

log = get_logger(__name__)

# One poll at a time, process-wide. A "Poll now" click during a scheduled tick
# would otherwise have two threads racing on the same activity: `import_ride`
# checks for the hash and then inserts, which is not atomic, and SQLite would
# answer the second writer with "database is locked" at best.
_poll_lock = threading.Lock()


@dataclass
class SyncResult:
    """What the caller needs to render a toast."""

    ok: bool
    message: str
    outcome: PollOutcome | None = None
    # True when the failure was unrecoverable and the poller switched itself
    # off, so the UI can say "reconnect" rather than "try again".
    reauthorize: bool = False


def credentials() -> ClientCredentials | None:
    """The app's client id/secret, or ``None`` if the environment lacks them."""
    return ClientCredentials.from_env()


def build_client(account: HammerheadAccount, creds: ClientCredentials) -> HammerheadClient:
    """An authenticated client whose refreshes land back in the database.

    The ``on_token_refresh`` callback is the whole reason this is not two
    lines at the call site. Hammerhead may rotate the refresh token on every
    exchange; a client that mints one and forgets it works fine until the
    access token expires, and then fails with a message that points nowhere.
    """
    tokens = _tokens_of(account)
    account_id = account.id

    def persist(new_tokens: TokenSet) -> None:
        # A fresh session on purpose: this fires mid-poll, inside whatever
        # transaction the caller has open, and the token must survive even if
        # that transaction is later rolled back.
        with rx.session() as session:
            row = session.get(HammerheadAccount, account_id) if account_id else None
            if row is None:
                return
            _apply_tokens(row, new_tokens)
            session.add(row)
            session.commit()

    return HammerheadClient(creds, tokens, on_token_refresh=persist)


def exchange_and_store(code: str) -> SyncResult:
    """Trade an authorization code for tokens and record the connection."""
    creds = credentials()
    if creds is None:
        return SyncResult(ok=False, message=_NOT_CONFIGURED)

    with rx.session() as session:
        account = get_account(session)
        client = HammerheadClient(creds)
        try:
            tokens = client.exchange_code(code)
        except HammerheadAuthError as exc:
            log.warning("hammerhead.connect.rejected", error=str(exc))
            return SyncResult(ok=False, message=f"Hammerhead rejected the code. {exc}")
        except HammerheadApiError as exc:
            log.warning("hammerhead.connect.failed", error=str(exc))
            return SyncResult(ok=False, message=f"Could not reach Hammerhead. {exc}")
        finally:
            client.close()

        _apply_tokens(account, tokens)
        account.connected_at = now_utc()
        account.oauth_state = None
        # A never-configured account would otherwise poll its entire history
        # on the first click, because `cursor_for` reads an unset date as
        # "everything". Only ever set when absent: a user who widened the
        # window before reconnecting keeps their choice.
        if account.backfill_start is None:
            account.backfill_start = (now_utc() - timedelta(days=DEFAULT_BACKFILL_DAYS)).date()
        session.add(account)
        session.commit()

    log.info("hammerhead.connect", user_id=tokens.user_id)
    return SyncResult(ok=True, message="Connected to Hammerhead.")


def disconnect() -> SyncResult:
    """Revoke at Hammerhead's end, then forget the tokens locally.

    Local state is cleared whatever the remote call does — see
    ``HammerheadClient.deauthorize``.
    """
    creds = credentials()
    with rx.session() as session:
        account = get_account(session)
        if creds is not None and account.connected:
            client = build_client(account, creds)
            try:
                client.deauthorize()
            finally:
                client.close()
        account.clear_connection()
        session.add(account)
        session.commit()

    log.info("hammerhead.disconnect")
    return SyncResult(ok=True, message="Disconnected from Hammerhead.")


def run_poll_once(*, max_downloads: int | None = None) -> SyncResult:
    """One poll, start to finish, with the status written back.

    Blocking: it parses FIT files and writes to SQLite. Callers on the event
    loop must hand it to a thread.
    """
    if not _poll_lock.acquire(blocking=False):
        log.info("hammerhead.poll.busy")
        return SyncResult(ok=False, message="A poll is already running.")
    try:
        return _run_poll_locked(max_downloads=max_downloads)
    finally:
        _poll_lock.release()


def _run_poll_locked(*, max_downloads: int | None) -> SyncResult:
    creds = credentials()
    if creds is None:
        return SyncResult(ok=False, message=_NOT_CONFIGURED)

    with rx.session() as session:
        account = get_account(session)
        if not account.connected:
            return SyncResult(ok=False, message="Not connected to Hammerhead.")

        since = cursor_for(account.backfill_start, account.cursor_date)
        client = build_client(account, creds)
        try:
            kwargs = {} if max_downloads is None else {"max_downloads": max_downloads}
            outcome = poll(session, client, since=since, **kwargs)
        except HammerheadAuthError as exc:
            # Unrecoverable: the token is revoked or expired beyond refresh.
            # Switching the poller off is the point — otherwise it retries
            # every interval for ever against an account that will never
            # answer, and the user never finds out why nothing imports.
            _record_failure(session, str(exc), disable=True)
            log.warning("hammerhead.poll.unauthorized", error=str(exc))
            return SyncResult(
                ok=False,
                message=f"Hammerhead rejected the connection: {exc}",
                reauthorize=True,
            )
        except HammerheadApiError as exc:
            # Transient. Leave `enabled` alone and try again next tick.
            _record_failure(session, str(exc), disable=False)
            log.warning("hammerhead.poll.failed", error=str(exc))
            return SyncResult(ok=False, message=f"Poll failed: {exc}")
        finally:
            client.close()

        _record_success(session, outcome)

    return SyncResult(ok=True, message=describe(outcome), outcome=outcome)


def describe(outcome: PollOutcome) -> str:
    """The status line, in the order that matters when reading it quickly."""
    parts = [f"{outcome.imported} imported"]
    if outcome.skipped:
        parts.append(f"{outcome.skipped} already held")
    if outcome.failed:
        parts.append(f"{outcome.failed} failed")
    if outcome.truncated:
        parts.append("more waiting for the next poll")
    return ", ".join(parts)


def _record_success(session: Session, outcome: PollOutcome) -> None:
    account = get_account(session)
    account.last_poll_at = now_utc()
    account.last_poll_ok = outcome.failed == 0
    account.last_poll_error = summarize_failures(outcome.failures)
    account.last_poll_imported = outcome.imported
    account.last_poll_skipped = outcome.skipped
    account.last_poll_failed = outcome.failed
    if outcome.next_cursor is not None:
        account.cursor_date = outcome.next_cursor
    session.add(account)
    session.commit()


def _record_failure(session: Session, error: str, *, disable: bool) -> None:
    account = get_account(session)
    account.last_poll_at = now_utc()
    account.last_poll_ok = False
    account.last_poll_error = error
    if disable:
        account.enabled = False
    session.add(account)
    session.commit()


def _tokens_of(account: HammerheadAccount) -> TokenSet | None:
    if not (account.access_token and account.refresh_token and account.token_expires_at):
        return None
    return TokenSet(
        access_token=account.access_token,
        refresh_token=account.refresh_token,
        expires_at=account.token_expires_at,
        user_id=account.user_id,
    )


def _apply_tokens(account: HammerheadAccount, tokens: TokenSet) -> None:
    account.access_token = tokens.access_token
    account.refresh_token = tokens.refresh_token
    account.token_expires_at = tokens.expires_at
    if tokens.user_id:
        account.user_id = tokens.user_id
    # What was actually granted, when the server says so. The consent screen
    # lets the user approve a *subset* of what was asked for, so recording the
    # request would let the card claim a scope that was declined while every
    # call 403s.
    account.scopes = tokens.scope or SCOPES


_NOT_CONFIGURED = (
    "Hammerhead is not configured. Set HAMMERHEAD_CLIENT_ID, "
    "HAMMERHEAD_CLIENT_SECRET and HAMMERHEAD_REDIRECT_URI."
)


def next_start_date(account: HammerheadAccount) -> date | None:
    """What the next poll will ask for, for the status card to show."""
    return cursor_for(account.backfill_start, account.cursor_date)


__all__ = [
    "HammerheadNotConfigured",
    "SyncResult",
    "build_client",
    "credentials",
    "describe",
    "disconnect",
    "exchange_and_store",
    "next_start_date",
    "run_poll_once",
]
