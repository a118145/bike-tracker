"""Each tool forwards one endpoint unchanged; failures come back structured.

The API is faked at the transport level (``httpx.MockTransport``), so the real
client code runs — URL building, the bearer header, error mapping — and the
tools are called through FastMCP's in-memory client, i.e. through the same
schema validation claude.ai's calls go through.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from bike_tracker_mcp import training_mcp
from bike_tracker_mcp.api_client import ApiClient

Handler = Callable[[httpx.Request], httpx.Response]


class FakeApi:
    def __init__(self, handler: Handler | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler or (
            lambda request: httpx.Response(200, json={"path": request.url.path})
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api = FakeApi()
    client = ApiClient("http://app:3000/api/v1", "api-token", transport=httpx.MockTransport(api))
    monkeypatch.setattr(training_mcp, "_api", client)
    return api


def call(tool: str, **arguments: Any) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with Client(training_mcp.mcp) as client:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
        assert result.structured_content is not None, result
        return dict(result.structured_content)

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("tool", "arguments", "path", "params"),
    [
        ("get_athlete_profile", {}, "/api/v1/athlete", {}),
        (
            "list_activities",
            {"from_date": "2026-09-28", "bike_id": 2},
            "/api/v1/activities",
            {"from_date": "2026-09-28", "bike_id": "2", "include_excluded": "false", "limit": "50"},
        ),
        ("get_activity_summary", {"activity_id": 7}, "/api/v1/activities/7", {}),
        (
            "get_activity_window_stats",
            {"activity_id": 7, "lap_index": 1, "skip_head_s": 300},
            "/api/v1/activities/7/window",
            {"lap_index": "1", "skip_head_s": "300.0", "skip_tail_s": "0.0"},
        ),
        (
            "get_activity_streams",
            {"activity_id": 7, "start_s": 0, "end_s": 600},
            "/api/v1/activities/7/streams",
            {
                "start_s": "0.0",
                "end_s": "600.0",
                "step_s": "5",
                "channels": "power_w,hr_bpm,cadence_rpm",
            },
        ),
        ("list_routes", {}, "/api/v1/routes", {}),
        (
            "get_route_history",
            {"route_id": 3},
            "/api/v1/routes/3/history",
            {"include_excluded": "true"},
        ),
        ("get_fitness_history", {}, "/api/v1/fitness", {}),
    ],
)
def test_a_tool_forwards_its_endpoint_unchanged(
    fake: FakeApi, tool: str, arguments: dict[str, Any], path: str, params: dict[str, str]
) -> None:
    result = call(tool, **arguments)

    assert result == {"path": path}, "the API's JSON, untouched"
    (request,) = fake.requests
    assert request.url.path == path
    assert dict(request.url.params) == params, "None parameters are dropped, not sent empty"
    assert request.headers["authorization"] == "Bearer api-token"


def test_get_version_adds_what_this_server_expects(fake: FakeApi) -> None:
    result = call("get_version")
    assert result["path"] == "/api/v1/version"
    assert result["mcp_version"] == training_mcp.MCP_VERSION
    assert result["expected_api_version"] == "v1"


def _with(status: int, body: Any) -> Handler:
    return lambda request: httpx.Response(status, json=body)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            422,
            {"detail": {"error": "too big", "suggested_step_s": 3}},
            {"error": "too big", "suggested_step_s": 3, "status": 422},
        ),
        (
            404,
            {"detail": {"error": "Activity 9 not found."}},
            {"error": "Activity 9 not found.", "status": 404},
        ),
        (
            422,
            {"detail": [{"loc": ["query", "limit"], "msg": "too large"}]},
            {"error": [{"loc": ["query", "limit"], "msg": "too large"}], "status": 422},
        ),
        (
            503,
            {"detail": {"error": "The API is disabled"}},
            {"error": "The API is disabled", "status": 503},
        ),
    ],
)
def test_api_errors_come_back_as_results_not_exceptions(
    monkeypatch: pytest.MonkeyPatch, status: int, body: Any, expected: dict[str, Any]
) -> None:
    client = ApiClient(
        "http://app:3000/api/v1", "t", transport=httpx.MockTransport(_with(status, body))
    )
    monkeypatch.setattr(training_mcp, "_api", client)
    assert call("get_activity_summary", activity_id=9) == expected


def test_an_unreachable_app_is_a_result_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = ApiClient("http://app:3000/api/v1", "t", transport=httpx.MockTransport(refuse))
    monkeypatch.setattr(training_mcp, "_api", client)
    result = call("list_routes")
    assert result["status"] is None
    assert "unreachable" in result["error"]


# ---------------------------------------------------------------------------
# Inbound auth
# ---------------------------------------------------------------------------

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "secret", "Basic secret"])
def test_the_endpoint_refuses_a_missing_or_wrong_token(authorization: str | None) -> None:
    headers = dict(MCP_HEADERS)
    if authorization is not None:
        headers["authorization"] = authorization
    with TestClient(training_mcp.create_app("secret")) as client:
        response = client.post("/mcp", headers=headers, content=json.dumps(INITIALIZE))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_the_endpoint_serves_mcp_with_the_token() -> None:
    headers = {**MCP_HEADERS, "authorization": "Bearer secret"}
    with TestClient(training_mcp.create_app("secret")) as client:
        response = client.post("/mcp", headers=headers, content=json.dumps(INITIALIZE))
    assert response.status_code == 200
    assert "bike-tracker" in response.text


def test_the_healthcheck_needs_no_token_and_says_nothing() -> None:
    with TestClient(training_mcp.create_app("secret")) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_server_refuses_to_start_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(training_mcp.MCP_TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        training_mcp.main()
    assert exit_info.value.code == 1
