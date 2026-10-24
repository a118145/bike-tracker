"""Ride tags, defined once in Settings and attached to rides.

The note explains, tags act (functionality review, 2026-10-11). A tag is a
global definition — name, colour and an optional *effect* — and a ride can only
carry tags that exist here. That keeps the vocabulary small enough for the MCP
layer to hand an LLM, and stops "not training" and "Not Training" from becoming
two tags that mean the same thing.

**Behaviour hangs off ``effect``, never off the name.** Renaming "Not training"
to anything else must not change which rides count in analysis; matching on a
name would make a cosmetic edit a data change.
"""

from datetime import UTC, datetime

from pydantic import field_validator
from reflex.model import ModelRegistry
from sqlmodel import Field, SQLModel

# The only effect so far: the ride drops out of everything that describes
# training (AGENTS.md rule 42). `hr_invalid` / `power_invalid` will join it.
TAG_EFFECT_EXCLUDE = "exclude_from_analysis"

# Effect value → label in the Settings dialog. "No effect" is `None` on the
# model and is not listed here.
TAG_EFFECTS: dict[str, str] = {TAG_EFFECT_EXCLUDE: "Exclude from analysis"}

# Radix colour scales offered for a tag. A fixed list rather than free input,
# because the value goes straight into `color_scheme`.
TAG_COLORS: tuple[str, ...] = (
    "gray",
    "red",
    "orange",
    "amber",
    "green",
    "teal",
    "blue",
    "purple",
    "pink",
)

TAG_NAME_MAX_LENGTH = 40


@ModelRegistry.register
class Tag(SQLModel, table=True):
    """A tag definition. Names are unique ignoring case (checked on save)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: str = Field(default="gray")
    effect: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@ModelRegistry.register
class RideTag(SQLModel, table=True):
    """A tag on a ride. The composite key makes a duplicate impossible."""

    ride_id: int = Field(foreign_key="ride.id", primary_key=True)
    tag_id: int = Field(foreign_key="tag.id", primary_key=True, index=True)


class TagForm(SQLModel):
    """Non-table form model for the tag dialog."""

    name: str = Field(min_length=1, max_length=TAG_NAME_MAX_LENGTH)
    color: str = "gray"
    effect: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Reject whitespace-only names and store the trimmed one."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name must not be blank.")
        return stripped

    @field_validator("color")
    @classmethod
    def _known_color(cls, value: str) -> str:
        if value not in TAG_COLORS:
            raise ValueError("Pick one of the offered colours.")
        return value

    @field_validator("effect")
    @classmethod
    def _known_effect(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if value not in TAG_EFFECTS:
            raise ValueError("Unknown effect.")
        return value


def match_tag(query: str, names: list[str]) -> str | None:
    """The defined tag name ``query`` refers to, or ``None``.

    Case-insensitive and trimmed, so "not training " finds "Not training".
    Returns the stored spelling, which is what the caller looks up.
    """
    wanted = query.strip().casefold()
    if not wanted:
        return None
    for name in names:
        if name.casefold() == wanted:
            return name
    return None
