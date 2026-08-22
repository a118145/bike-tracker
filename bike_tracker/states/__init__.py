"""Application states.

Rules that hold for every state in this package:

* Subclass ``rx.State`` **directly**. There is no shared base state.
* Share across states with ``await self.get_state(Other)``, never inheritance.
* ``NavState`` is the one exception to living here — it belongs to
  ``navigation/`` because it is meaningless without ``nav_config``.
"""

from .ride_detail_state import RideDetailState
from .rides_state import RidesState

__all__ = ["RideDetailState", "RidesState"]
