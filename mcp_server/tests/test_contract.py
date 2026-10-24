"""The docstrings document the API contract, key for key (AGENTS.md rule 46).

``openapi_v1.json`` is generated from the app and kept current by the app's
own test suite. This side checks that every key a tool can return is named in
that tool's description — the description as FastMCP registered it, which
also catches a docstring written as an f-string (registered as no
description at all).
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from bike_tracker_mcp import training_mcp

SPEC = json.loads((Path(__file__).resolve().parent.parent / "openapi_v1.json").read_text())
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]

TOOL_PATHS = {
    "get_version": "/api/v1/version",
    "get_athlete_profile": "/api/v1/athlete",
    "list_activities": "/api/v1/activities",
    "get_activity_summary": "/api/v1/activities/{activity_id}",
    "get_activity_window_stats": "/api/v1/activities/{activity_id}/window",
    "get_activity_streams": "/api/v1/activities/{activity_id}/streams",
    "list_routes": "/api/v1/routes",
    "get_route_history": "/api/v1/routes/{route_id}/history",
    "get_fitness_history": "/api/v1/fitness",
}


def _descriptions() -> dict[str, str]:
    async def run() -> dict[str, str]:
        async with Client(training_mcp.mcp) as client:
            return {tool.name: tool.description or "" for tool in await client.list_tools()}

    return asyncio.run(run())


DESCRIPTIONS = _descriptions()


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if "$ref" in node else []
        for value in node.values():
            found += _refs(value)
        return found
    if isinstance(node, list):
        return [ref for item in node for ref in _refs(item)]
    return []


def _keys(schema_name: str, seen: frozenset[str] = frozenset()) -> set[str]:
    schema = SCHEMAS[schema_name]
    keys = set(schema.get("properties", {}))
    for ref in _refs(schema.get("properties", {})):
        name = ref.rsplit("/", 1)[-1]
        if name not in seen:
            keys |= _keys(name, seen | {schema_name})
    return keys


def _response_keys(path: str) -> set[str]:
    ref = SPEC["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    return _keys(ref.rsplit("/", 1)[-1])


def test_every_endpoint_has_exactly_one_tool_and_every_tool_is_registered() -> None:
    assert sorted(TOOL_PATHS.values()) == sorted(SPEC["paths"])
    assert set(DESCRIPTIONS) == set(TOOL_PATHS)


@pytest.mark.parametrize("tool", sorted(TOOL_PATHS))
def test_the_docstring_names_every_returned_key(tool: str) -> None:
    description = DESCRIPTIONS[tool]
    missing = sorted(
        key
        for key in _response_keys(TOOL_PATHS[tool])
        if not re.search(rf"\b{re.escape(key)}\b", description)
    )
    assert missing == [], f"{tool} does not document {missing}"


@pytest.mark.parametrize("tool", sorted(TOOL_PATHS))
def test_the_docstring_has_the_required_sections(tool: str) -> None:
    description = DESCRIPTIONS[tool]
    for section in ("When to call:", "Returns:", "Runtime:"):
        assert section in description, f"{tool} lacks {section!r}"
