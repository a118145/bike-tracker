"""Ride notes: one definition of empty, one write path (AGENTS.md rule 44).

The note explains and never acts, so besides the form and the write path these
tests pin that saving a note moves no number and no ride in or out of analysis.
"""

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, create_engine, select

from bike_tracker.ingest.seed import seed_bikes
from bike_tracker.ingest.sources.file import import_file
from bike_tracker.models import (
    Ride,
    RideNote,
    RideNoteForm,
    analysis_rides,
    load_note_summaries,
    note_summary,
    save_ride_note,
)
from bike_tracker.models.ride import NOTE_TOOLTIP_MAX_CHARS
from bike_tracker.states.ride_detail_state import NONE_OPTION
from bike_tracker.states.rides_state import _to_card
from bike_tracker.states.route_detail_state import _to_row
from bike_tracker.utils.validation import parse_form

from .conftest import EVENING_FIT

# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------


def _form(**fields: str) -> dict[str, str]:
    """What the dialog submits when nothing is touched, overridden by ``fields``."""
    return {"text": "", "sleep_quality": "", "rpe": "", "perceived_breathing_limit": ""} | fields


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_blank_text_is_no_text(text: str) -> None:
    parsed, errors = parse_form(_form(text=text), RideNoteForm)
    assert errors == {}
    assert parsed is not None
    assert parsed.text is None
    assert parsed.is_empty


def test_the_form_trims_text_and_casts_the_scales() -> None:
    parsed, errors = parse_form(
        _form(text="  HR strap dropped out.\n", sleep_quality="2", rpe="7"), RideNoteForm
    )
    assert errors == {}
    assert parsed is not None
    assert parsed.text == "HR strap dropped out."
    assert (parsed.sleep_quality, parsed.rpe, parsed.perceived_breathing_limit) == (2, 7, None)
    assert not parsed.is_empty


def test_a_quick_field_alone_is_a_note() -> None:
    parsed, _ = parse_form(_form(perceived_breathing_limit="4"), RideNoteForm)
    assert parsed is not None
    assert not parsed.is_empty


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sleep_quality", "0"),
        ("sleep_quality", "6"),
        ("rpe", "0"),
        ("rpe", "11"),
        ("perceived_breathing_limit", "6"),
        ("rpe", "hard"),
        ("text", "x" * 4001),
    ],
)
def test_the_form_rejects(field: str, value: str) -> None:
    parsed, errors = parse_form(_form(**{field: value}), RideNoteForm)
    assert parsed is None
    assert field in errors


def test_the_sentinel_is_not_a_value() -> None:
    """The select's "—" must be mapped back before validation, or it is an error."""
    _, errors = parse_form(_form(rpe=NONE_OPTION), RideNoteForm)
    assert "rpe" in errors


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _note(**fields: object) -> RideNote:
    return RideNote(ride_id=1, **fields)


def test_summary_of_text_only() -> None:
    assert note_summary(_note(text="Headwind both ways.")) == "Headwind both ways."


def test_summary_of_fields_only() -> None:
    note = _note(sleep_quality=2, rpe=7, perceived_breathing_limit=4)
    assert note_summary(note) == "Sleep 2/5 · RPE 7/10 · Breathing 4/5"


def test_summary_skips_fields_that_were_not_recorded() -> None:
    assert note_summary(_note(rpe=3)) == "RPE 3/10"


def test_a_list_gets_the_first_line_and_the_ride_page_all_of_it() -> None:
    note = _note(text="HR strap dropped out.\nPower looked fine.", rpe=6)
    assert note_summary(note) == "HR strap dropped out.\nRPE 6/10"
    assert note_summary(note, full_text=True) == (
        "HR strap dropped out.\nPower looked fine.\nRPE 6/10"
    )


def test_a_long_note_is_cut_for_the_tooltip() -> None:
    summary = note_summary(_note(text="a" * 1000), full_text=True)
    assert len(summary) == NOTE_TOOLTIP_MAX_CHARS + 1
    assert summary.endswith("…")


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def _import_one(session: Session) -> Ride:
    seed_bikes(session)
    import_file(EVENING_FIT, session)
    return session.exec(select(Ride)).one()


def _notes(session: Session) -> list[RideNote]:
    return list(session.exec(select(RideNote)).all())


def test_saving_creates_then_updates_one_row(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride_id = _import_one(session).id or 0

        save_ride_note(session, ride_id, RideNoteForm(text="First", rpe=5))
        save_ride_note(session, ride_id, RideNoteForm(text="Second"))

        notes = _notes(session)
        assert len(notes) == 1
        assert notes[0].text == "Second"
        assert notes[0].rpe is None, "a field cleared in the form must clear in the row"
    engine.dispose()


def test_an_empty_save_deletes_the_note(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride_id = _import_one(session).id or 0
        save_ride_note(session, ride_id, RideNoteForm(text="Something"))

        assert save_ride_note(session, ride_id, RideNoteForm()) is None
        assert _notes(session) == []
        assert load_note_summaries(session) == {}
    engine.dispose()


def test_an_empty_save_on_a_ride_without_a_note_writes_nothing(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride_id = _import_one(session).id or 0
        assert save_ride_note(session, ride_id, RideNoteForm()) is None
        assert _notes(session) == []
    engine.dispose()


def test_a_hand_recorded_temperature_survives_an_edit(db_url: str) -> None:
    """The form has no temperature field, so saving must leave the column alone."""
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride_id = _import_one(session).id or 0
        session.add(RideNote(ride_id=ride_id, text="Old", temperature_c=4.5))
        session.commit()

        save_ride_note(session, ride_id, RideNoteForm(text="New"))
        assert _notes(session)[0].temperature_c == 4.5
    engine.dispose()


def test_a_note_moves_no_number_and_no_ride(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride = _import_one(session)
        ride_id = ride.id or 0
        before = ride.model_dump()

        save_ride_note(session, ride_id, RideNoteForm(text="Not training, really", sleep_quality=1))

        session.expire_all()
        after = session.get(Ride, ride_id)
        assert after is not None
        assert after.model_dump() == before
        assert [r.id for r in session.exec(analysis_rides()).all()] == [ride_id]
    engine.dispose()


def test_summaries_come_per_ride_and_only_for_rides_with_a_note(db_url: str) -> None:
    engine = create_engine(db_url)
    with Session(engine) as session:
        ride_id = _import_one(session).id or 0
        save_ride_note(session, ride_id, RideNoteForm(text="One\nTwo", rpe=4))

        assert load_note_summaries(session) == {ride_id: "One\nRPE 4/10"}
        assert load_note_summaries(session, [ride_id]) == {ride_id: "One\nRPE 4/10"}
        assert load_note_summaries(session, [ride_id + 1]) == {}
        assert load_note_summaries(session, []) == {}
    engine.dispose()


# ---------------------------------------------------------------------------
# View models
# ---------------------------------------------------------------------------


def _ride(ride_id: int = 7) -> Ride:
    start = datetime(2026, 10, 24, 7, 30, tzinfo=UTC)
    return Ride(id=ride_id, fit_hash="x", start_time=start, end_time=start)


def test_the_card_and_the_table_row_carry_the_summary() -> None:
    notes = {7: "Headwind"}
    assert _to_card(_ride(), {}, {}, notes=notes).note_summary == "Headwind"
    assert _to_card(_ride(8), {}, {}, notes=notes).note_summary == ""
    assert _to_row(_ride(), note_summary="Headwind").note_summary == "Headwind"
    assert _to_row(_ride()).note_summary == ""
