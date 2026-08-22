"""Route constants — the only place a URL string is written.

Nested routes compose from their parent so a path is spelled once. The
``[...]`` segments are Reflex dynamic args: declaring a page at
``/rides/[ride_id]`` injects a ``ride_id`` var (always ``str``) into every
state class.
"""

HOME_ROUTE = "/"  # Dashboard
ROUTES_ROUTE = "/routes"
ROUTE_DETAIL_ROUTE = ROUTES_ROUTE + "/[route_id]"
RIDES_ROUTE = "/rides"
RIDE_DETAIL_ROUTE = RIDES_ROUTE + "/[ride_id]"
FITNESS_ROUTE = "/fitness"
BIKES_ROUTE = "/bikes"
NOT_FOUND_ROUTE = "/404"
