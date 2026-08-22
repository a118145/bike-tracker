"""Training zones derived from FTP and LTHR.

Both schemes are the conventional ones so the numbers match what the rider
sees elsewhere: Coggan's seven power zones as fractions of FTP, and Friel's
five heart-rate zones as fractions of lactate-threshold heart rate.

The top zone of each scheme is open-ended; it is returned with ``None`` as its
upper bound rather than an arbitrary large number, so the UI can render "440+"
instead of a made-up ceiling.
"""

from dataclasses import dataclass

# (name, description, lower fraction, upper fraction or None for open-ended)
POWER_ZONE_BANDS: list[tuple[str, str, float, float | None]] = [
    ("Z1", "Active recovery", 0.00, 0.55),
    ("Z2", "Endurance", 0.55, 0.75),
    ("Z3", "Tempo", 0.75, 0.90),
    ("Z4", "Threshold", 0.90, 1.05),
    ("Z5", "VO2 max", 1.05, 1.20),
    ("Z6", "Anaerobic", 1.20, 1.50),
    ("Z7", "Neuromuscular", 1.50, None),
]

HR_ZONE_BANDS: list[tuple[str, str, float, float | None]] = [
    ("Z1", "Recovery", 0.00, 0.85),
    ("Z2", "Aerobic", 0.85, 0.89),
    ("Z3", "Tempo", 0.89, 0.94),
    ("Z4", "Threshold", 0.94, 1.00),
    ("Z5", "VO2 max", 1.00, None),
]


@dataclass(frozen=True)
class Zone:
    """One training zone with its absolute bounds."""

    name: str
    description: str
    low: int
    high: int | None  # None = open-ended top zone

    @property
    def label(self) -> str:
        """Range as shown in the UI, e.g. ``"207-242"`` or ``"345+"``."""
        if self.high is None:
            return f"{self.low}+"
        return f"{self.low}-{self.high}"


def _build(
    bands: list[tuple[str, str, float, float | None]],
    reference: float,
) -> list[Zone]:
    """Turn fractional bands into absolute, gap-free zones.

    Each zone starts one unit above the previous zone's top, so every wattage
    or heart rate falls in exactly one zone. Rounding the fractions
    independently would otherwise leave holes — at FTP 250, Z2 ends at 188 and
    Z3 would start at 188 too, putting one value in two zones.
    """
    if reference <= 0:
        return []

    zones: list[Zone] = []
    for name, description, low, high in bands:
        upper = None if high is None else round(reference * high)
        lower = round(reference * low) if not zones else (zones[-1].high or 0) + 1
        zones.append(Zone(name=name, description=description, low=lower, high=upper))
    return zones


def power_zones(ftp_w: float) -> list[Zone]:
    """Coggan power zones in watts for a given FTP."""
    return _build(POWER_ZONE_BANDS, ftp_w)


def hr_zones(lthr_bpm: float) -> list[Zone]:
    """Friel heart-rate zones in bpm for a given lactate threshold."""
    return _build(HR_ZONE_BANDS, lthr_bpm)
