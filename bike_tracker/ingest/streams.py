"""Encoding and decoding of :class:`~..models.RideStream` channel blobs.

One ride is ~10,000 samples across ten channels. Stored as rows that is a
table in the tens of millions within a season; stored as ten compressed
``float32`` buffers it is a few kilobytes read in a single primary-key
lookup — which is what the detail page's chart needs and nothing more.

``float32`` throughout, including for heart rate and cadence: NaN is how a
missing sample is spelled, and an integer array has no way to say it.
"""

import zlib
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

from ..metrics import FloatArray

IntArray = npt.NDArray[np.intp]

# Channels that round-trip through RideStream, in the order the detail page
# tends to want them. Names match both the RideStream columns and the keys
# ingest.fit produces, so the mapping stays a straight attribute copy.
STREAM_CHANNELS: tuple[str, ...] = (
    "time_s",
    "lat",
    "lon",
    "altitude_m",
    "distance_m",
    "speed_ms",
    "power_w",
    "hr_bpm",
    "cadence_rpm",
    "temperature_c",
    "grade_pct",
    "left_balance_pct",
)

# zlib level 6 is the default trade-off and already gets a 1 Hz altitude trace
# down by an order of magnitude; level 9 buys a few percent for noticeably
# more CPU on every import.
COMPRESSION_LEVEL = 6


def encode(values: FloatArray | Sequence[float | None] | None) -> bytes | None:
    """Compress one channel to a stored blob, or None if it has no data."""
    if values is None:
        return None
    array = np.asarray(
        values if isinstance(values, np.ndarray) else [np.nan if v is None else v for v in values],
        dtype=np.float32,
    )
    if array.size == 0 or not np.isfinite(array).any():
        return None
    return zlib.compress(array.astype("<f4").tobytes(), COMPRESSION_LEVEL)


def decode(blob: bytes | None) -> FloatArray:
    """Expand a stored blob back to a ``float64`` array.

    Returns an empty array for a missing channel, so callers can treat "no
    power meter" and "no samples" the same way instead of branching on None.
    """
    if not blob:
        return np.empty(0, dtype=np.float64)
    raw = np.frombuffer(zlib.decompress(blob), dtype="<f4")
    return raw.astype(np.float64)


def downsample_indices(count: int, max_points: int) -> IntArray:
    """Evenly spaced sample indices, capped at ``max_points``.

    Used for chart traces rather than for storage. A 1 Hz three-hour ride is
    10,000 points per channel; drawing five of those channels at full rate
    ships megabytes of JSON to the browser for a plot a few hundred pixels
    wide, where the extra points cannot be seen.
    """
    if count <= max_points or max_points <= 0:
        return np.arange(count)
    return np.unique(np.linspace(0, count - 1, max_points).astype(int))
