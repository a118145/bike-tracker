"""Figure → JSON-safe dict, keeping trace uids.

Reflex has a serializer for ``go.Figure`` and it cannot be used here: it calls
``plotly.io.to_json(figure)``, whose ``remove_uids`` argument defaults to
``True`` (``reflex_base/utils/serializers.py``). Every ``uid`` is therefore
stripped on the way to the browser, Plotly falls back to matching traces by
position, and a figure whose trace count changes reads as one trace destroyed
and another created — ``removeLayer`` and ``addSource`` on a MapLibre style
that may not be loaded. See AGENTS.md rule 31.

So the plot state vars hold the already-serialised ``dict`` rather than the
``go.Figure``, and this is the one place that conversion happens. Going
through ``to_json`` rather than ``Figure.to_plotly_json`` matters: the latter
hands back the numpy arrays plotly stores internally, which Reflex's JSON
encoder cannot write.
"""

import json
from typing import Any

import plotly.graph_objects as go
from plotly.io import to_json


def figure_to_dict(figure: go.Figure) -> dict[str, Any]:
    """Serialize a figure for a plot prop, preserving every trace ``uid``."""
    result: dict[str, Any] = json.loads(str(to_json(figure, remove_uids=False)))
    return result
