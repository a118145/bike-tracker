"""Bearer-token check for every API route — fail closed.

Prod serves the GUI and ``/api`` on one port behind Traefik, so the API sits on
a LAN-reachable host even though only the MCP container is meant to call it.
The token comes from the environment, like the Hammerhead credentials
(AGENTS.md rule 35), and is read per request so a test can set it.

**No token configured means no API** (503), never an open one: forgetting the
variable on the server must not be how the ride diary ends up readable by
anything on the LAN.
"""

import hmac
import os

from fastapi import Header, HTTPException, status

TOKEN_ENV = "BIKE_TRACKER_API_TOKEN"


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get(TOKEN_ENV, "").strip()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": f"The API is disabled: {TOKEN_ENV} is not set on the server."},
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token.strip().encode(), expected.encode()
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Missing or invalid bearer token."},
            headers={"WWW-Authenticate": "Bearer"},
        )
