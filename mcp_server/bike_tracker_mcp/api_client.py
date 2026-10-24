"""The one door to bike-tracker: its REST API, with a bearer token.

This package never imports the app (AGENTS.md rule 48). It is a separate
image; the only thing it knows about bike-tracker is ``/api/v1`` and the
contract in ``openapi_v1.json``.

Every failure becomes a structured ``{"error": ..., "status": ...}`` result
rather than an exception (mcp-guidelines): an unreachable homeserver or a bad
parameter is something the model should read and react to, not a crash.
"""

import os
from typing import Any

import httpx

API_URL_ENV = "BIKE_TRACKER_API_URL"
API_TOKEN_ENV = "BIKE_TRACKER_API_TOKEN"
# The compose service name and the app's single port (docker/compose.example.yml).
DEFAULT_API_URL = "http://bike-tracker:3000/api/v1"
TIMEOUT_S = 30.0


class ApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

    @classmethod
    def from_env(cls) -> "ApiClient":
        return cls(
            os.environ.get(API_URL_ENV, "").strip() or DEFAULT_API_URL,
            os.environ.get(API_TOKEN_ENV, "").strip(),
        )

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET ``path`` and return the JSON body, or a structured error."""
        query = {key: value for key, value in params.items() if value is not None}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=TIMEOUT_S,
                transport=self._transport,
            ) as client:
                response = await client.get(path, params=query)
        except httpx.HTTPError as exc:
            return {"error": f"bike-tracker is unreachable: {exc}", "status": None}

        try:
            body = response.json()
        except ValueError:
            body = None

        if response.status_code != 200:
            detail = body.get("detail") if isinstance(body, dict) else None
            if isinstance(detail, dict) and "error" in detail:
                return {**detail, "status": response.status_code}
            message = detail if detail is not None else response.text[:500]
            return {"error": message, "status": response.status_code}

        if not isinstance(body, dict):
            return {"error": "bike-tracker returned no JSON object.", "status": 200}
        return body
