"""The Hammerhead connection (masterplan §2.6.2).

One row, ever. There is exactly one Karoo account behind this app, so a
singleton beats a settings key-value table: every field is typed, the form
binds to it directly, and Alembic sees a schema rather than a bag of strings.
:func:`get_account` is the only way to reach it and creates it on first use,
so no migration has to seed a row.

**What is deliberately not here: the client id and the client secret.** Those
are app-level, identical on every run, and a secret in a form field is a
secret on screen and in a database backup. They come from the environment —
see ``ingest/hammerhead_client.ClientCredentials``. Only the rotating tokens
live here, because only they change.
"""

from datetime import UTC, date, datetime

from reflex.model import ModelRegistry
from sqlmodel import Field, Session, SQLModel, select

# How often the poller runs when nothing says otherwise. The Karoo uploads
# when it finds wifi, so anything under a few minutes is just noise.
DEFAULT_POLL_INTERVAL_MIN = 30

# How far back a freshly connected account reaches on its first poll.
# `cursor_for` reads an unset `backfill_start` as "the whole account", so
# without a default the very first *Poll now* walks several seasons of
# history — the opposite of a safe default. Applied at connect time rather
# than as the column default, so a user who deliberately clears the date
# still gets everything.
DEFAULT_BACKFILL_DAYS = 30


@ModelRegistry.register
class HammerheadAccount(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    # --- connection --------------------------------------------------------
    # Nullable as a set: all present means connected, all absent means not.
    access_token: str | None = Field(default=None)
    refresh_token: str | None = Field(default=None)
    token_expires_at: datetime | None = Field(default=None)
    user_id: str | None = Field(default=None)
    scopes: str | None = Field(default=None)
    connected_at: datetime | None = Field(default=None)

    # The `state` handed to the authorize URL, kept so the value coming back
    # can be checked against it. Cleared as soon as it is used or abandoned.
    oauth_state: str | None = Field(default=None)

    # --- configuration -----------------------------------------------------
    # Off until switched on. A poller that starts itself the moment a token
    # appears cannot be developed against safely.
    enabled: bool = Field(default=False)
    poll_interval_min: int = Field(default=DEFAULT_POLL_INTERVAL_MIN)

    # How far back the first poll reaches. Chosen by the user, because the
    # alternative is a first run that downloads several seasons.
    backfill_start: date | None = Field(default=None)

    # --- cursor and last-run status ---------------------------------------
    # The `startDate` the next poll asks for. Always behind the newest
    # activity seen by CURSOR_OVERLAP_DAYS; see sources/hammerhead.py.
    cursor_date: date | None = Field(default=None)

    last_poll_at: datetime | None = Field(default=None)
    last_poll_ok: bool | None = Field(default=None)
    last_poll_error: str | None = Field(default=None)
    last_poll_imported: int = Field(default=0)
    last_poll_skipped: int = Field(default=0)
    last_poll_failed: int = Field(default=0)

    @property
    def connected(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    def clear_connection(self) -> None:
        """Forget the tokens without forgetting the settings.

        The interval, the backfill date and the cursor survive a disconnect on
        purpose: reconnecting the same account should not re-import a season,
        and must not silently re-narrow a window the user widened.
        """
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.user_id = None
        self.scopes = None
        self.connected_at = None
        self.oauth_state = None
        self.enabled = False


def get_account(session: Session) -> HammerheadAccount:
    """The one row, created on first use.

    Not seeded by the migration: a migration that inserts data has to be
    correct on an existing database as well as a fresh one, and this costs one
    query to avoid that entirely.
    """
    account = session.exec(select(HammerheadAccount)).first()
    if account is None:
        account = HammerheadAccount()
        session.add(account)
        session.commit()
        session.refresh(account)
    return account


def now_utc() -> datetime:
    """Naive UTC, matching every other timestamp column in this schema.

    SQLite drops tzinfo on write, so a mixture of aware and naive values only
    shows up later as a comparison that raises.
    """
    return datetime.now(UTC).replace(tzinfo=None)
