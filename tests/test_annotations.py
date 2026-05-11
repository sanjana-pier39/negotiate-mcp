"""
test_annotations.py — CI guard against shipping un-annotated MCP tools.

Drop this into mcp-connector/tests/ and wire it into your CI. Fails the
build if any registered tool is missing the required MCP annotation hints.

Why: Missing tool annotations is the #1 cause of Anthropic Connectors
Directory rejections (~30% of submissions). Don't let it happen twice.

Run:
    pip install pytest mcp
    pytest tests/test_annotations.py -v
"""

from __future__ import annotations

import pytest

# Import the MCP server instance so its tools are registered.
from negotiate_mcp.server import mcp  # noqa: E402


# Every tool must have at least these annotation keys present.
REQUIRED_KEYS = {"readOnlyHint"}

# Strongly recommended for write tools (readOnlyHint is False).
RECOMMENDED_FOR_WRITES = {"destructiveHint", "idempotentHint", "openWorldHint"}

# Strongly recommended for any tool that touches external systems.
RECOMMENDED_FOR_OPEN_WORLD = {"openWorldHint"}


def _list_registered_tools():
    """
    Return a list of (name, annotations_dict) tuples for every tool the
    FastMCP instance currently has registered.

    FastMCP's internal API has shifted across versions. Try the modern path
    first, then fall back to known older shapes.
    """
    # Modern: mcp.list_tools() returns coroutine; for a sync test, we read
    # the underlying tool registry directly.
    candidates = []

    for attr in ("_tools", "tools", "_tool_registry"):
        registry = getattr(mcp, attr, None)
        if registry:
            if isinstance(registry, dict):
                candidates = list(registry.items())
            elif isinstance(registry, list):
                candidates = [(t.name, t) for t in registry]
            break

    if not candidates:
        # Last resort: inspect the tool manager.
        tool_manager = getattr(mcp, "_tool_manager", None)
        if tool_manager:
            tools = getattr(tool_manager, "_tools", {}) or {}
            candidates = list(tools.items())

    if not candidates:
        pytest.fail(
            "Could not enumerate FastMCP tools — the SDK internals may have "
            "changed. Update _list_registered_tools() to match your installed "
            "mcp package version."
        )

    out = []
    for name, tool in candidates:
        # FastMCP wraps the original function in a Tool object that has an
        # `annotations` attribute (dict or None).
        annotations = getattr(tool, "annotations", None)
        if annotations is None:
            annotations = {}
        elif hasattr(annotations, "model_dump"):
            # Pydantic ToolAnnotations object — dump to dict.
            annotations = annotations.model_dump(exclude_none=True)
        out.append((name, annotations))
    return out


def test_every_tool_has_annotations():
    """Every tool must have a non-empty annotations block."""
    tools = _list_registered_tools()
    assert tools, "No tools registered — did the import fail?"
    missing = [name for name, ann in tools if not ann]
    assert not missing, (
        f"These tools have no annotations and will be rejected by the "
        f"Anthropic Connectors Directory: {missing}.\n"
        f"Add an `annotations={{...}}` block to each `@mcp.tool(...)` decorator."
    )


def test_every_tool_has_readonly_hint():
    """`readOnlyHint` is the most-checked annotation. Make it explicit on every tool."""
    tools = _list_registered_tools()
    missing = [name for name, ann in tools if "readOnlyHint" not in ann]
    assert not missing, (
        f"These tools are missing `readOnlyHint`: {missing}. "
        f"Set it to True for read-only tools, False for tools that change state."
    )


def test_write_tools_have_destructive_hint():
    """For tools that write, destructiveHint must be present (True or False)."""
    tools = _list_registered_tools()
    bad = [
        name for name, ann in tools
        if ann.get("readOnlyHint") is False and "destructiveHint" not in ann
    ]
    assert not bad, (
        f"These write tools are missing `destructiveHint`: {bad}. "
        f"Set it to True if the tool may delete/overwrite, False if it's additive."
    )


def test_write_tools_have_idempotent_hint():
    """For tools that write, idempotentHint should be set."""
    tools = _list_registered_tools()
    missing = [
        name for name, ann in tools
        if ann.get("readOnlyHint") is False and "idempotentHint" not in ann
    ]
    if missing:
        pytest.fail(
            f"These write tools are missing `idempotentHint`: {missing}. "
            f"Strongly recommended for retry-safety reasoning by the model."
        )


def test_open_world_tools_declared():
    """Tools that hit external systems should declare openWorldHint."""
    # Negotiate-MCP: every tool except a hypothetical pure-local utility is open-world.
    tools = _list_registered_tools()
    missing = [name for name, ann in tools if "openWorldHint" not in ann]
    if missing:
        pytest.fail(
            f"These tools are missing `openWorldHint`: {missing}. "
            f"Set to True for any tool that calls an external HTTP endpoint."
        )


def test_expected_tool_set():
    """Sanity check: we expect exactly six tools today."""
    tools = _list_registered_tools()
    names = {n for n, _ in tools}
    expected = {
        "find_stores", "discover_store", "list_products",
        "start_negotiation", "send_message", "read_history",
    }
    extra = names - expected
    missing = expected - names
    assert not missing, f"Expected tools missing from server: {missing}"
    assert not extra, (
        f"New tool(s) registered without updating this test: {extra}. "
        f"Add them to `expected` (and add their annotations to server.py)."
    )


def test_annotations_match_published_audit():
    """Ground-truth check: annotations match what we said in AUDIT.md."""
    expected = {
        "find_stores":      {"readOnlyHint": True,  "idempotentHint": True,  "openWorldHint": True},
        "discover_store":   {"readOnlyHint": True,  "idempotentHint": True,  "openWorldHint": True},
        "list_products":    {"readOnlyHint": True,  "idempotentHint": True,  "openWorldHint": True},
        "start_negotiation":{"readOnlyHint": False, "idempotentHint": False, "openWorldHint": True},
        "send_message":     {"readOnlyHint": False, "idempotentHint": False, "openWorldHint": True},
        "read_history":     {"readOnlyHint": True,  "idempotentHint": True,  "openWorldHint": True},
    }
    actual = {n: ann for n, ann in _list_registered_tools()}
    for name, must_have in expected.items():
        got = actual.get(name, {})
        for key, want in must_have.items():
            assert got.get(key) == want, (
                f"Tool {name!r} has {key}={got.get(key)!r}, "
                f"audit says it should be {want!r}."
            )
