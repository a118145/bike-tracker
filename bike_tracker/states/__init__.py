"""Application states.

Rules that hold for every state in this package:

* Subclass ``rx.State`` **directly**. There is no shared base state.
* Share across states with ``await self.get_state(Other)``, never inheritance.
* ``NavState`` is the one exception to living here — it belongs to
  ``navigation/`` because it is meaningless without ``nav_config``.
"""

from .bikes_state import BikesState
from .dashboard_state import DashboardState
from .fitness_state import FitnessState
from .ride_detail_state import RideDetailState
from .rides_state import RidesState
from .route_detail_state import RouteDetailState
from .routes_state import RoutesState

__all__ = [
    "BikesState",
    "DashboardState",
    "FitnessState",
    "RideDetailState",
    "RidesState",
    "RouteDetailState",
    "RoutesState",
]
