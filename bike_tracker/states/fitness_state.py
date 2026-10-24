"""Fitness view state (masterplan §2.5).

Four things, all derived from the ``FitnessEval`` history plus the per-ride
power curve that import already stored:

* power and heart-rate zone tables from the current FTP and LTHR,
* the current values and when they were measured,
* FTP and baseline over time,
* mean-maximal power curves, one per consecutive 30- or 90-day window.

The last one needs no stream decoding: every ride carries its own curve in the
eight ``best_power_*_w`` columns, so the page is a database read and a
max-reduce (:mod:`metrics.trends`).

Flat state, per AGENTS.md rule 2. Figures are stored already-serialised, as on
the ride pages — Reflex's own ``go.Figure`` serializer strips trace uids.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import plotly.graph_objects as go
import reflex as rx
from sqlmodel import col, select

from .. import metrics
from ..components.toast import notify_error, notify_success
from ..components.zone_table import ZoneRow, zone_rows
from ..ingest.importer import refresh_ftp_dependent
from ..metrics import EffortWindow
from ..models import (
    CURVE_WINDOW_DAYS,
    EVAL_BASELINE,
    EVAL_FTP,
    EVAL_LTHR,
    FitnessEval,
    Ride,
    analysis_rides,
    latest_evaluations,
    power_curve_windows,
)
from ..utils.formatting import EMPTY, format_date, format_number
from ..utils.logging import get_logger
from ..utils.plotly_json import figure_to_dict

log = get_logger(__name__).bind(state="FitnessState")

# Window lengths offered by the power-curve toggle (masterplan §2.5). The
# lengths themselves live in models/fitness.py, shared with the REST API.
WINDOW_CHOICES = tuple(str(days) for days in CURVE_WINDOW_DAYS)

FTP_COLOUR = "#f59e0b"
BASELINE_COLOUR = "#14b8a6"
# Most recent window first; later ones fade back so the newest curve reads as
# the current state and the rest as history.
CURVE_COLOURS = ("#2563eb", "#7c9fe0", "#a8bde8", "#cdd8ef")


@dataclass
class EvalRow:
    """One entry in the evaluation history table."""

    id: int = 0
    kind: str = ""
    date: str = ""
    value_text: str = ""
    source: str = ""


@dataclass
class CurveRow:
    """One window in the max-power table beneath the curve chart."""

    label: str = ""
    ride_count: str = ""
    # Not named `values`: a Var attribute of that name collides with the dict
    # method of the same name when the row is subscripted inside rx.foreach.
    cells: list[str] = field(default_factory=list)


class FitnessState(rx.State):
    loading: bool = True

    current_ftp: str = EMPTY
    current_ftp_date: str = ""
    current_lthr: str = EMPTY
    current_lthr_date: str = ""
    current_baseline: str = EMPTY
    current_baseline_date: str = ""

    power_zones: list[ZoneRow] = []
    hr_zones: list[ZoneRow] = []
    history: list[EvalRow] = []

    ftp_figure: dict = {}
    baseline_figure: dict = {}
    has_ftp_history: bool = False
    has_baseline_history: bool = False
    curve_figure: dict = {}
    curve_rows: list[CurveRow] = []
    curve_headers: list[str] = []

    window_choice: str = "30"

    # --- manual entry dialog (FTP / LTHR seeding, masterplan §2.5) --------
    entry_open: bool = False
    entry_type: str = EVAL_LTHR
    entry_value: str = ""
    entry_error: str = ""
    entry_form_key: int = 0

    @rx.var
    def has_ftp(self) -> bool:
        return self.current_ftp != EMPTY

    @rx.var
    def has_lthr(self) -> bool:
        return self.current_lthr != EMPTY

    @rx.var
    def entry_title(self) -> str:
        return "Set FTP" if self.entry_type == EVAL_FTP else "Set LTHR"

    @rx.var
    def entry_unit(self) -> str:
        return "W" if self.entry_type == EVAL_FTP else "bpm"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @rx.event
    def load(self):
        """Page ``on_load`` — rebuild every card and figure."""
        self.loading = True
        yield

        self._reload()
        self.loading = False

    def _reload(self) -> None:
        with rx.session() as session:
            evaluations = session.exec(
                select(FitnessEval).order_by(col(FitnessEval.valid_from))
            ).all()
            rides = session.exec(analysis_rides().order_by(col(Ride.start_time))).all()
            windows = power_curve_windows(
                rides,
                window_days=int(self.window_choice),
                now=datetime.now(UTC).replace(tzinfo=None),
            )

        self._apply_current(list(evaluations))
        self._build_history(list(evaluations))
        self._build_history_figures(list(evaluations))
        self._build_curves(windows)
        log.info("fitness.load", evaluations=len(evaluations), rides=len(rides))

    def _apply_current(self, evaluations: list[FitnessEval]) -> None:
        """Latest value of each type, and the zone tables that follow from it."""
        latest = latest_evaluations(evaluations)

        ftp = latest.get(EVAL_FTP)
        lthr = latest.get(EVAL_LTHR)
        base = latest.get(EVAL_BASELINE)

        self.current_ftp = f"{format_number(ftp.value)}" if ftp else EMPTY
        self.current_ftp_date = format_date(ftp.valid_from) if ftp else ""
        self.current_lthr = f"{format_number(lthr.value)}" if lthr else EMPTY
        self.current_lthr_date = format_date(lthr.valid_from) if lthr else ""
        self.current_baseline = f"{format_number(base.value, 2)}" if base else EMPTY
        self.current_baseline_date = format_date(base.valid_from) if base else ""

        # `power_zones`/`hr_zones` return [] for a non-positive reference, so a
        # missing FTP yields an empty table rather than a band of zeros.
        self.power_zones = zone_rows(metrics.power_zones(ftp.value if ftp else 0.0))
        self.hr_zones = zone_rows(metrics.hr_zones(lthr.value if lthr else 0.0))

    def _build_history(self, evaluations: list[FitnessEval]) -> None:
        self.history = [
            EvalRow(
                id=record.id or 0,
                kind=_kind_label(record.type),
                date=format_date(record.valid_from),
                value_text=_value_text(record),
                source="From a ride" if record.ride_id else "Entered by hand",
            )
            for record in reversed(evaluations)
        ]

    def _build_history_figures(self, evaluations: list[FitnessEval]) -> None:
        self.has_ftp_history = any(e.type == EVAL_FTP for e in evaluations)
        self.has_baseline_history = any(e.type == EVAL_BASELINE for e in evaluations)
        self.ftp_figure = figure_to_dict(
            _history_figure(
                [e for e in evaluations if e.type == EVAL_FTP],
                uid="ftp",
                name="FTP",
                unit="W",
                colour=FTP_COLOUR,
            )
        )
        self.baseline_figure = figure_to_dict(
            _history_figure(
                [e for e in evaluations if e.type == EVAL_BASELINE],
                uid="baseline",
                name="Baseline",
                unit="W/bpm",
                colour=BASELINE_COLOUR,
            )
        )

    def _build_curves(self, windows: list[EffortWindow]) -> None:
        figure = go.Figure()
        for index, window in enumerate(windows):
            if window.is_empty:
                continue
            xs = [d for d in metrics.POWER_CURVE_WINDOWS_S if window.best.get(d) is not None]
            ys = [window.best[d] for d in xs]
            if not xs:
                continue
            figure.add_trace(
                go.Scatter(
                    # Stable uid per window slot: the trace count changes with
                    # the 30/90 toggle, and positional matching would reshuffle
                    # every curve's identity (AGENTS.md rule 31).
                    uid=f"curve-{index}",
                    x=xs,
                    y=ys,
                    mode="lines+markers",
                    name=window.label,
                    line={"color": CURVE_COLOURS[index % len(CURVE_COLOURS)], "width": 2},
                )
            )
        figure.update_layout(
            margin={"l": 48, "r": 16, "t": 8, "b": 40},
            height=320,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=True,
            legend={"orientation": "h", "y": 1.14, "x": 0},
            hovermode="x unified",
            xaxis={
                "type": "log",
                "title": "Duration (s)",
                "tickvals": list(metrics.POWER_CURVE_WINDOWS_S),
                "ticktext": [_duration_label(d) for d in metrics.POWER_CURVE_WINDOWS_S],
                "showgrid": False,
            },
            yaxis={"title": "W", "gridcolor": "rgba(156,163,175,0.2)"},
        )
        self.curve_figure = figure_to_dict(figure)

        self.curve_headers = [_duration_label(d) for d in metrics.POWER_CURVE_WINDOWS_S]
        self.curve_rows = [
            CurveRow(
                label=window.label,
                ride_count=f"{window.ride_count} ride"
                if window.ride_count == 1
                else f"{window.ride_count} rides",
                cells=[
                    format_number(window.best[d]) if window.best.get(d) is not None else EMPTY
                    for d in metrics.POWER_CURVE_WINDOWS_S
                ],
            )
            for window in windows
        ]

    @rx.event
    def set_window_choice(self, value: str | list[str]):
        choice = value[0] if isinstance(value, list) else value
        if choice not in WINDOW_CHOICES:
            return
        self.window_choice = choice
        self._reload()

    # ------------------------------------------------------------------
    # Manual entry
    # ------------------------------------------------------------------

    @rx.event
    def open_entry(self, kind: str):
        """Enter an FTP or LTHR by hand.

        LTHR has no ride-based evaluation (§2.4.2 offers only FTP and
        baseline), so without this the heart-rate zone card could never be
        filled. It doubles as a way to seed the history before any ride has
        been evaluated.
        """
        self.entry_type = kind
        self.entry_value = ""
        self.entry_error = ""
        self.entry_form_key += 1
        self.entry_open = True

    @rx.event
    def set_entry_open(self, value: bool):
        self.entry_open = value
        if not value:
            self.entry_error = ""

    @rx.event
    def set_entry_value(self, value: str):
        self.entry_value = value
        self.entry_error = ""

    @rx.event
    def save_entry(self):
        """Store a hand-entered value and refresh everything it affects."""
        raw = self.entry_value.strip()
        try:
            value = float(raw)
        except ValueError:
            self.entry_error = "Enter a number."
            return None
        if value <= 0:
            self.entry_error = "Must be greater than zero."
            return None

        # Dated to now: a hand-entered value says "this is true from today",
        # and back-dating it would rewrite the load of rides already scored.
        now = datetime.now(UTC).replace(tzinfo=None)
        with rx.session() as session:
            session.add(
                FitnessEval(
                    type=self.entry_type,
                    value=value,
                    valid_from=now,
                    start_time=now,
                    end_time=now,
                )
            )
            session.commit()
            refresh_ftp_dependent(session, now)

        log.info("fitness.entry.save", kind=self.entry_type, value=value)
        self.entry_open = False
        self._reload()
        return notify_success(f"{_kind_label(self.entry_type)} set to {format_number(value)}.")

    @rx.event
    def delete_entry(self, eval_id: int):
        with rx.session() as session:
            record = session.get(FitnessEval, eval_id)
            if record is None:
                return notify_error("That entry no longer exists.")
            valid_from = record.valid_from
            session.delete(record)
            session.commit()
            refresh_ftp_dependent(session, valid_from)

        log.info("fitness.entry.delete", eval_id=eval_id)
        self._reload()
        return notify_success("Entry deleted.")


# ---------------------------------------------------------------------------
# View-model helpers
# ---------------------------------------------------------------------------


def _kind_label(kind: str) -> str:
    return {EVAL_FTP: "FTP", EVAL_BASELINE: "Baseline", EVAL_LTHR: "LTHR"}.get(kind, kind)


def _value_text(record: FitnessEval) -> str:
    if record.type == EVAL_BASELINE:
        return f"{format_number(record.value, 2)} W/bpm"
    unit = "W" if record.type == EVAL_FTP else "bpm"
    return f"{format_number(record.value)} {unit}"


def _duration_label(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def _history_figure(
    records: list[FitnessEval],
    *,
    uid: str,
    name: str,
    unit: str,
    colour: str,
) -> go.Figure:
    """One evaluation type over time.

    Real ``datetime`` x-values on a date axis — the ``seconds_to_clock`` trick
    in ``ride_chart`` exists only because ride time has no calendar date, and
    does not apply here.
    """
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            uid=uid,
            x=[record.valid_from for record in records],
            y=[record.value for record in records],
            mode="lines+markers",
            name=name,
            line={"color": colour, "width": 2},
            hovertemplate=f"%{{y}} {unit}<extra>%{{x|%Y-%m-%d}}</extra>",
        )
    )
    # A single point has no range, and Plotly then autoscales the date axis
    # down to microseconds — "10:46:41.8835" as a tick label on a chart of
    # months. Give it a month either side so one evaluation reads as a date.
    axis: dict[str, object] = {
        "type": "date",
        "showgrid": False,
        "tickformat": "%b %Y",
    }
    if len(records) == 1:
        only = records[0].valid_from
        axis["range"] = [
            (only - timedelta(days=30)).isoformat(),
            (only + timedelta(days=30)).isoformat(),
        ]

    figure.update_layout(
        margin={"l": 48, "r": 16, "t": 8, "b": 32},
        height=220,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis=axis,
        yaxis={"title": unit, "gridcolor": "rgba(156,163,175,0.2)"},
    )
    return figure
