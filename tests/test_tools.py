"""
Tests for tool conformance, capability, and error suites.

Runs each suite against both the good fixture (expect pass/skip/warn)
and the bad fixture (expect specific failures).
"""

from __future__ import annotations

import pytest

from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.capabilities import CapabilitiesSuite
from mcp_test_harness.suites.errors import ErrorsSuite
from mcp_test_harness.suites.tools import ToolsSuite


# ------------------------------------------------------------------ #
# CapabilitiesSuite — good server
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_capabilities_good_server(good_server_cmd):
    config = ServerConfig(command=good_server_cmd)
    suite = CapabilitiesSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}

    cap_result = by_name.get("tools_capability_declared")
    assert cap_result is not None
    assert cap_result.status == "pass", (
        f"Expected tools_capability_declared to pass: {cap_result.detail}"
    )


# ------------------------------------------------------------------ #
# ToolsSuite — good server
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_tools_suite_good_server(good_server_cmd):
    config = ServerConfig(command=good_server_cmd)
    suite = ToolsSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}

    must_pass = [
        "tools_list_returns_array",
        "tool_has_required_fields",
        "tool_name_valid",
        "input_schema_valid_jsonschema",
        "tool_call_valid",
        "tool_call_unknown_name",
        "tool_call_missing_required_args",
    ]
    failures = []
    for name in must_pass:
        r = by_name.get(name)
        if r is None:
            failures.append(f"{name}: missing from results")
        elif r.status not in ("pass", "warn", "skip"):
            failures.append(f"{name}: {r.status} — {r.detail}")

    assert not failures, "\n".join(failures)


# ------------------------------------------------------------------ #
# ToolsSuite — bad server: specific expected failures
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_tools_suite_bad_server_null_schema(bad_server_cmd):
    """Bad server has a tool with null inputSchema — tool_has_required_fields should fail."""
    config = ServerConfig(command=bad_server_cmd)
    suite = ToolsSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    r = by_name.get("tool_has_required_fields")
    assert r is not None
    assert r.status == "fail", (
        f"Expected tool_has_required_fields to fail against bad server, got {r.status}: {r.detail}"
    )


@pytest.mark.asyncio
async def test_tools_suite_bad_server_invalid_name(bad_server_cmd):
    """Bad server has a tool with spaces in name — tool_name_valid should fail."""
    config = ServerConfig(command=bad_server_cmd)
    suite = ToolsSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    r = by_name.get("tool_name_valid")
    assert r is not None
    assert r.status == "fail", (
        f"Expected tool_name_valid to fail against bad server, got {r.status}: {r.detail}"
    )


# ------------------------------------------------------------------ #
# ErrorsSuite — good server
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_errors_suite_good_server(good_server_cmd):
    config = ServerConfig(command=good_server_cmd)
    suite = ErrorsSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}

    must_pass_or_warn = [
        "method_not_found",
        "response_id_matches_request",
        "response_has_result_xor_error",
    ]
    failures = []
    for name in must_pass_or_warn:
        r = by_name.get(name)
        if r is None:
            failures.append(f"{name}: missing from results")
        elif r.status not in ("pass", "warn"):
            failures.append(f"{name}: {r.status} — {r.detail}")

    assert not failures, "\n".join(failures)


@pytest.mark.asyncio
async def test_errors_suite_good_server_parse_error(good_server_cmd):
    """
    Verify parse_error test runs without crashing against the good server.

    FastMCP sends a notifications/message log entry instead of a -32700 error
    response when it receives invalid JSON — this is a known conformance gap.
    The test should detect this and report fail/warn (not error/crash).
    """
    config = ServerConfig(command=good_server_cmd)
    suite = ErrorsSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    r = by_name.get("parse_error")
    assert r is not None
    # Any deterministic status is acceptable here — the key thing is no crash
    assert r.status in ("pass", "warn", "fail"), (
        f"parse_error test crashed unexpectedly: {r.status}: {r.detail}"
    )


@pytest.mark.asyncio
async def test_errors_suite_bad_server_parse_error(bad_server_cmd):
    """Bad server silently drops invalid JSON — parse_error test should fail."""
    config = ServerConfig(command=bad_server_cmd)
    suite = ErrorsSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    r = by_name.get("parse_error")
    assert r is not None
    assert r.status == "fail", (
        f"Expected parse_error to fail against bad server, got {r.status}: {r.detail}"
    )
