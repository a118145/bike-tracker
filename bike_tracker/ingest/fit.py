"""FIT parsing — bytes in, plain dataclasses out.

No database, no Reflex, no models. The Hammerhead poller hands its downloaded
bytes to this same :func:`parse`, which is what keeps everything downstream
source-agnostic.

FIT stores coordinates as semicircles and speeds in m/s; both are normalised
here so nothing further up the stack has to know the wire format.
"""

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import fitdecode

# FIT semicircles: a signed 32-bit sweep of the full 360°.
SEMICIRCLE_TO_DEGREES = 180.0 / 2**31

# Channels lifted out of the `record` messages, mapped to our own names.
RECORD_CHANNELS: dict[str, tuple[str, ...]] = {
    "lat": ("position_lat",),
    "lon": ("position_long",),
    "altitude_m": ("enhanced_altitude", "altitude"),
    "distance_m": ("distance",),
    "speed_ms": ("enhanced_speed", "speed"),
    "power_w": ("power",),
    "hr_bpm": ("heart_rate",),
    "cadence_rpm": ("cadence",),
    "temperature_c": ("temperature",),
    "grade_pct": ("grade",),
    "left_balance_pct": ("left_right_balance",),
}

_SEMICIRCLE_FIELDS = {"position_lat", "position_long"}


@dataclass
class ParsedLap:
    """One `lap` message."""

    index: int
    start_time: datetime
    end_time: datetime
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDevice:
    """One `device_info` message — the source of bike auto-detection."""

    device_index: Any = None
    manufacturer: str | None = None
    product_name: str | None = None
    ant_device_number: int | None = None
    source_type: str | None = None
    serial_number: int | None = None


@dataclass
class ParsedRide:
    """Everything one FIT activity file has to say.

    ``channels`` holds one list per record field, all of the same length, with
    ``None`` where a sample had no value. ``session`` and the per-lap
    ``fields`` are the raw FIT keys — the importer maps them, so the naming
    translation lives in exactly one place.
    """

    fit_hash: str
    start_time: datetime
    end_time: datetime
    session: dict[str, Any] = field(default_factory=dict)
    channels: dict[str, list[Any]] = field(default_factory=dict)
    timestamps: list[datetime] = field(default_factory=list)
    laps: list[ParsedLap] = field(default_factory=list)
    devices: list[ParsedDevice] = field(default_factory=list)
    sport: str = "cycling"

    @property
    def sample_count(self) -> int:
        return len(self.timestamps)

    @property
    def ant_ids(self) -> list[int]:
        """ANT+ device numbers of every paired sensor in this activity."""
        return [d.ant_device_number for d in self.devices if d.ant_device_number is not None]

    def time_offsets_s(self) -> list[float]:
        """Seconds elapsed since the first record, per sample."""
        if not self.timestamps:
            return []
        origin = self.timestamps[0].timestamp()
        return [t.timestamp() - origin for t in self.timestamps]


class FitParseError(ValueError):
    """The payload is not a FIT activity we can use."""


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _frame_values(frame: fitdecode.FitDataMessage) -> dict[str, Any]:
    return {f.name: f.value for f in frame.fields}


def _convert(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _SEMICIRCLE_FIELDS:
        return float(value) * SEMICIRCLE_TO_DEGREES
    return value


def parse(data: bytes) -> ParsedRide:
    """Decode a FIT activity file.

    Args:
        data: The raw file contents. Nothing here writes them anywhere — FIT
            files are deliberately never persisted (masterplan §1.3), so the
            SHA-256 of these bytes is what later deduplicates re-imports.

    Raises:
        FitParseError: The file has no usable records or no session message.
    """
    fit_hash = hashlib.sha256(data).hexdigest()

    session: dict[str, Any] = {}
    timestamps: list[datetime] = []
    raw_records: list[dict[str, Any]] = []
    laps: list[ParsedLap] = []
    devices: list[ParsedDevice] = []
    sport = "cycling"

    try:
        with fitdecode.FitReader(io.BytesIO(data)) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                values = _frame_values(frame)

                if frame.name == "record":
                    stamp = _as_utc(values.get("timestamp"))
                    if stamp is None:
                        continue
                    timestamps.append(stamp)
                    raw_records.append(values)
                elif frame.name == "session":
                    session = values
                    sport = str(values.get("sport") or sport)
                elif frame.name == "lap":
                    start = _as_utc(values.get("start_time"))
                    end = _as_utc(values.get("timestamp"))
                    if start is None or end is None:
                        continue
                    laps.append(
                        ParsedLap(index=len(laps), start_time=start, end_time=end, fields=values)
                    )
                elif frame.name == "device_info":
                    devices.append(
                        ParsedDevice(
                            device_index=values.get("device_index"),
                            manufacturer=_str_or_none(values.get("manufacturer")),
                            product_name=_str_or_none(values.get("product_name")),
                            ant_device_number=_int_or_none(values.get("ant_device_number")),
                            source_type=_str_or_none(values.get("source_type")),
                            serial_number=_int_or_none(values.get("serial_number")),
                        )
                    )
                elif frame.name == "sport":
                    sport = str(values.get("sport") or sport)
    except fitdecode.FitError as exc:
        raise FitParseError(f"Not a readable FIT file: {exc}") from exc

    if not timestamps:
        raise FitParseError("FIT file contains no record messages.")

    channels: dict[str, list[Any]] = {}
    for target, candidates in RECORD_CHANNELS.items():
        column = [_first_present(record, candidates) for record in raw_records]
        if any(value is not None for value in column):
            channels[target] = column

    start_time = _as_utc(session.get("start_time")) or timestamps[0]
    end_time = _as_utc(session.get("timestamp")) or timestamps[-1]

    # Deduplicate device_info: the Karoo re-emits its own creator record every
    # few minutes with an updated battery level, which would otherwise make a
    # ten-message list out of two real sensors.
    devices = _unique_devices(devices)

    return ParsedRide(
        fit_hash=fit_hash,
        start_time=start_time,
        end_time=end_time,
        session=session,
        channels=channels,
        timestamps=timestamps,
        laps=laps,
        devices=devices,
        sport=sport,
    )


def _first_present(record: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    """First non-None value among ``candidates``.

    The ``enhanced_*`` variants come first in every tuple: FIT's original
    16-bit altitude and speed fields saturate, and the device writes both.
    """
    for name in candidates:
        value = record.get(name)
        if value is not None:
            return _convert(name, value)
    return None


def _unique_devices(devices: list[ParsedDevice]) -> list[ParsedDevice]:
    seen: set[tuple[Any, int | None, int | None]] = set()
    unique: list[ParsedDevice] = []
    for device in devices:
        key = (device.device_index, device.ant_device_number, device.serial_number)
        if key in seen:
            continue
        seen.add(key)
        unique.append(device)
    return unique


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


# "Karoo-Evening_Ride-2026-08-19-1723.fit" -> "Evening Ride"
_FILENAME_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9]+-)?(?P<name>.+?)-\d{4}-\d{2}-\d{2}-\d{4}$",
)


def name_from_filename(filename: str) -> str | None:
    """Recover the activity name the Karoo encoded in the file name.

    FIT activity files carry no name field, but the Karoo exports them as
    ``Karoo-<Activity Name>-<date>-<time>.fit`` and masterplan §2.4.1 wants
    that name on the ride card. Returns None if the name cannot be recovered,
    leaving the importer to generate one from the start time.
    """
    stem = filename.rsplit("/", 1)[-1]
    if stem.lower().endswith(".fit"):
        stem = stem[:-4]
    match = _FILENAME_PATTERN.match(stem)
    if not match:
        return None
    name = match.group("name").replace("_", " ").strip()
    return name or None
