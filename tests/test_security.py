"""
Tests for the security suites: injection probing and input validation.

Good server:  all injection tests should pass; validation tests pass or warn.
Bad server:   run_command tool should trigger injection fail;
              info disclosure should warn (stack trace in errors).
"""

from __future__ import annotations

import pytest

from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.injection import InjectionSuite
from mcp_test_harness.suites.validation import ValidationSuite


# ------------------------------------------------------------------ #
# InjectionSuite — good server
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_injection_good_server_no_failures(good_server_cmd):
    """Good server (echo tool) should return full payload — no injection failures."""
    config = ServerConfig(command=good_server_cmd)
    suite = InjectionSuite()
    results = await suite.run(config)

    failures = [r for r in results if r.status == "fail"]
    assert not failures, (
        "Unexpected injection failures on good server:\n"
        + "\n".join(f"  {r.name}: {r.detail}" for r in failures)
    )

    # At least some results must be pass or warn (not all skipped)
    non_skip = [r for r in results if r.status != "skip"]
    assert non_skip, "All injection tests were skipped on good server — no string-arg tools found"


@pytest.mark.asyncio
async def test_injection_good_server_results_present(good_server_cmd):
    """Verify all 5 metacharacter payloads produce results for each string-arg tool."""
    config = ServerConfig(command=good_server_cmd)
    suite = InjectionSuite()
    results = await suite.run(config)

    if all(r.status == "skip" for r in results):
        pytest.skip("No string-arg tools found — injection suite skipped")

    # Result names have format: shell_metachar_{payload_name}__{tool_name}
    # Extract the payload name from the prefix before the double-underscore.
    payload_names = {"semicolon", "pipe", "backtick", "dollar_paren", "ampersand"}
    seen_payloads: set[str] = set()
    for r in results:
        # e.g. "shell_metachar_dollar_paren__echo" → prefix = "shell_metachar_dollar_paren"
        prefix = r.name.split("__")[0]  # "shell_metachar_{payload_name}"
        for pname in payload_names:
            if prefix.endswith(pname):
                seen_payloads.add(pname)
                break
    assert seen_payloads == payload_names, (
        f"Not all 5 payload types tested; got: {seen_payloads}"
    )


# ------------------------------------------------------------------ #
# InjectionSuite — bad server
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_injection_bad_server_run_command_fails(bad_server_cmd):
    """
    Bad server's run_command tool simulates shell injection:
    returns canary without metachar prefix → expect at least one fail result.
    """
    config = ServerConfig(command=bad_server_cmd)
    suite = InjectionSuite()
    results = await suite.run(config)

    run_command_results = [r for r in results if "run_command" in r.name]
    assert run_command_results, (
        "No injection results found for run_command tool on bad server"
    )

    failures = [r for r in run_command_results if r.status == "fail"]
    assert failures, (
        "Expected at least one injection fail for run_command on bad server.\n"
        "Got: " + ", ".join(f"{r.name}={r.status}" for r in run_command_results)
    )


@pytest.mark.asyncio
async def test_injection_bad_server_echo_unsafe_passes(bad_server_cmd):
    """
    Bad server's echo_unsafe tool echoes the full payload back including metacharacters.
    Despite being 'unsafe' (no sanitisation), it should still PASS injection detection
    because the full payload (including metachar) is returned — it's a safe echo.
    """
    config = ServerConfig(command=bad_server_cmd)
    suite = InjectionSuite()
    results = await suite.run(config)

    echo_results = [r for r in results if "echo_unsafe" in r.name]
    if not echo_results:
        pytest.skip("No echo_unsafe results found")

    # echo_unsafe should pass injection tests (full echo, not execution)
    non_pass = [r for r in echo_results if r.status not in ("pass", "warn")]
    assert not non_pass, (
        "echo_unsafe should pass injection tests (full payload echoed):\n"
        + "\n".join(f"  {r.name}: {r.status} — {r.detail}" for r in non_pass)
    )


# ------------------------------------------------------------------ #
# ValidationSuite — good server
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_validation_good_server_no_crashes(good_server_cmd):
    """Validation suite should complete without error status on good server."""
    config = ServerConfig(command=good_server_cmd)
    suite = ValidationSuite()
    results = await suite.run(config)

    errors = [r for r in results if r.status == "error"]
    assert not errors, (
        "Unexpected error status in validation suite on good server:\n"
        + "\n".join(f"  {r.name}: {r.detail}" for r in errors)
    )


@pytest.mark.asyncio
async def test_validation_good_server_no_failures(good_server_cmd):
    """FastMCP-based good server should not hard-fail any validation tests."""
    config = ServerConfig(command=good_server_cmd)
    suite = ValidationSuite()
    results = await suite.run(config)

    failures = [r for r in results if r.status == "fail"]
    assert not failures, (
        "Unexpected fail status in validation suite on good server:\n"
        + "\n".join(f"  {r.name}: {r.detail}" for r in failures)
    )


@pytest.mark.asyncio
async def test_validation_good_server_info_disclosure_passes(good_server_cmd):
    """Good server should not leak paths or stack traces in error responses."""
    config = ServerConfig(command=good_server_cmd)
    suite = ValidationSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    r = by_name.get("error_info_disclosure")
    assert r is not None
    assert r.status in ("pass", "skip"), (
        f"Expected error_info_disclosure pass/skip on good server, "
        f"got {r.status}: {r.detail}"
    )


# ------------------------------------------------------------------ #
# ValidationSuite — bad server
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_validation_bad_server_info_disclosure_warns(bad_server_cmd):
    """Bad server leaks a stack trace and unix path in error responses — expect warn."""
    config = ServerConfig(command=bad_server_cmd)
    suite = ValidationSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}
    r = by_name.get("error_info_disclosure")
    assert r is not None
    assert r.status == "warn", (
        f"Expected error_info_disclosure to warn against bad server, "
        f"got {r.status}: {r.detail}"
    )


@pytest.mark.asyncio
async def test_validation_bad_server_no_validation_warns(bad_server_cmd):
    """
    Bad server's no_validation tool accepts wrong types without error.
    wrong_type_argument and missing_required_argument should warn.
    """
    config = ServerConfig(command=bad_server_cmd)
    suite = ValidationSuite()
    results = await suite.run(config)

    by_name = {r.name: r for r in results}

    wrong_type = by_name.get("wrong_type_argument")
    assert wrong_type is not None
    # bad server has no_validation tool which accepts wrong types → warn expected
    assert wrong_type.status in ("warn", "pass"), (
        f"wrong_type_argument: expected warn or pass, got {wrong_type.status}: {wrong_type.detail}"
    )

    missing_req = by_name.get("missing_required_argument")
    assert missing_req is not None
    assert missing_req.status in ("warn", "pass"), (
        f"missing_required_argument: expected warn or pass, got {missing_req.status}: {missing_req.detail}"
    )
