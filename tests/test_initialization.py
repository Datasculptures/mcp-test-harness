"""
Tests for the initialization suite.

Runs the suite against both the good fixture (expect all pass/warn)
and the bad fixture (expect specific failures).
"""

from __future__ import annotations

import pytest

from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.initialization import InitializationSuite


@pytest.mark.asyncio
async def test_init_suite_good_server(good_server_cmd):
    config = ServerConfig(command=good_server_cmd)
    suite = InitializationSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}

    # These must pass against the known-good server
    must_pass = [
        "initialize_response_valid",
        "version_negotiation",
        "initialized_notification_accepted",
        "ping_before_initialized",
        "ping_during_operation",
        "stdout_purity",
        "shutdown_clean",
    ]
    failures = []
    for name in must_pass:
        r = by_name.get(name)
        if r is None:
            failures.append(f"{name}: missing from results")
        elif r.status not in ("pass", "warn"):
            failures.append(f"{name}: {r.status} — {r.detail}")

    assert not failures, "\n".join(failures)


@pytest.mark.asyncio
async def test_init_suite_bad_server_version(bad_server_cmd):
    """Bad server reports wrong protocolVersion — should be a warn (negotiation allowed)
    but the version '1999-01-01' is not a known valid version so may be warn."""
    config = ServerConfig(command=bad_server_cmd)
    suite = InitializationSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    version_result = by_name.get("version_negotiation")
    assert version_result is not None
    # Bad server returns "1999-01-01" — harness should warn, not crash
    assert version_result.status in ("warn", "pass"), (
        f"Expected warn for bad version, got {version_result.status}: {version_result.detail}"
    )


@pytest.mark.asyncio
async def test_init_suite_bad_server_stdout_purity(bad_server_cmd):
    """Bad server emits non-JSON to stdout — should fail stdout_purity."""
    config = ServerConfig(command=bad_server_cmd)
    suite = InitializationSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    purity = by_name.get("stdout_purity")
    assert purity is not None
    assert purity.status == "fail", (
        f"Expected stdout_purity to fail against bad server, got {purity.status}: {purity.detail}"
    )
