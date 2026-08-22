"""Pure calculations — the one import boundary in this project.

**Nothing in this package may import ``reflex`` or ``..models``.** Arrays and
dataclasses in, scalars out (brief §3.3).

That is not architectural decoration. It is the reason the physics and
training-metric tests run in milliseconds with no app, no database and no
fixtures — which in turn is what makes it practical to check every derived
number against the values a real Karoo wrote into a real FIT file. Everything
outside this package may be as Reflex-flavoured as it likes.
"""

from .arrays import (
    FloatArray,
    as_float_array,
    centered_mean,
    channel_stats,
    mean_active,
    moving_time_axis,
    rolling_mean,
    sample_intervals,
    window_indices,
)
from .elevation import (
    GAIN_THRESHOLD_M,
    VAM_SMOOTH_WINDOW,
    elevation_gain_loss,
    grade,
    vam,
)
from .geo import (
    PREVIEW_PADDING,
    PREVIEW_VIEWBOX,
    Point,
    bounding_box,
    bounds_to_center_zoom,
    haversine_m,
    polyline_length_m,
    project_svg_points,
    resample_polyline,
    route_match_score,
    simplify_polyline,
    to_points,
)
from .power import (
    GROSS_EFFICIENCY,
    NP_WINDOW_S,
    BestWindow,
    baseline,
    best_20min_window,
    calories_kcal,
    decoupling,
    hr_tss,
    intensity_factor,
    normalized_power,
    total_work_kj,
    tss,
)
from .zones import HR_ZONE_BANDS, POWER_ZONE_BANDS, Zone, hr_zones, power_zones

__all__ = [
    "GAIN_THRESHOLD_M",
    "GROSS_EFFICIENCY",
    "HR_ZONE_BANDS",
    "NP_WINDOW_S",
    "POWER_ZONE_BANDS",
    "PREVIEW_PADDING",
    "PREVIEW_VIEWBOX",
    "VAM_SMOOTH_WINDOW",
    "BestWindow",
    "FloatArray",
    "Point",
    "Zone",
    "as_float_array",
    "baseline",
    "best_20min_window",
    "bounding_box",
    "bounds_to_center_zoom",
    "calories_kcal",
    "centered_mean",
    "channel_stats",
    "decoupling",
    "elevation_gain_loss",
    "grade",
    "haversine_m",
    "hr_tss",
    "hr_zones",
    "intensity_factor",
    "mean_active",
    "moving_time_axis",
    "normalized_power",
    "polyline_length_m",
    "power_zones",
    "project_svg_points",
    "resample_polyline",
    "rolling_mean",
    "route_match_score",
    "sample_intervals",
    "simplify_polyline",
    "to_points",
    "total_work_kj",
    "tss",
    "vam",
    "window_indices",
]
