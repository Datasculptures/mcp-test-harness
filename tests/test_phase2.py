"""
Phase 2 integration tests.

Covers the new suites (path_traversal, resource_scope, operational),
env var probes in injection, nested/array tests in validation, and CLI changes.

Follows the same patterns as test_integration.py.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from mcp_test_harness.cli import _run, parse_args
from tests.conftest import GOOD_SERVER_PATH as GOOD_SERVER, BAD_SERVER_PATH as BAD_SERVER

PYTHON = sys.executable


def _args(argv: list[str]):
    return parse_args(argv)


# ------------------------------------------------------------------ #
# CLI — suite selection
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_suite_selection_operational_only():
    """--suite operational includes only the operational suite."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "operational", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    suite_names = set(report["suites"].keys())

    assert "operational" in suite_names
    assert "initialization" not in suite_names
    assert "injection" not in suite_names
    assert "path_traversal" not in suite_names


@pytest.mark.asyncio
async def test_suite_selection_security_includes_new_suites():
    """--suite security includes path_traversal and resource_scope."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    suite_names = set(report["suites"].keys())

    assert "injection" in suite_names
    assert "validation" in suite_names
    assert "path_traversal" in suite_names
    assert "resource_scope" in suite_names
    # Conformance suites must not appear
    assert "initialization" not in suite_names
    assert "tools" not in suite_names


@pytest.mark.asyncio
async def test_full_run_includes_operational_suite():
    """Default (all) run includes the operational suite in the report."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--format", "json", "-o", path,
        "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    assert "operational" in report["suites"]
    assert exit_code in (0, 1), "Harness error (exit 2) on good server"


# ------------------------------------------------------------------ #
# Path traversal suite
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_path_traversal_good_server_no_harness_error():
    """path_traversal against good_server completes without harness error."""
    exit_code = await _run(_args([
        "stdio", "--suite", "security",
        "--", PYTHON, str(GOOD_SERVER),
    ]))
    assert exit_code in (0, 1), "Harness error (exit 2) on good server path_traversal"


@pytest.mark.asyncio
async def test_path_traversal_bad_server_detects_failure():
    """path_traversal against bad_server detects the read_file traversal vulnerability."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(BAD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))

    pt = report["suites"].get("path_traversal", {})
    tests = pt.get("tests", [])
    # At least one traversal test should fail (read_file serves passwd content)
    failed_tests = [t for t in tests if t["status"] == "fail"]
    assert failed_tests, (
        "path_traversal suite should detect at least one failure against bad_server "
        f"(got: {[t['name'] + ':' + t['status'] for t in tests]})"
    )


# ------------------------------------------------------------------ #
# Resource scope suite
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_resource_scope_good_server_skips_or_passes():
    """
    resource_scope against good_server: either skips (no resources capability)
    or passes all tests. It must not error.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    rs = report["suites"].get("resource_scope", {})
    summary = rs.get("summary", {})

    assert summary.get("errored", 0) == 0, (
        f"resource_scope should not error on good server: {rs}"
    )
    assert exit_code in (0, 1)


# ------------------------------------------------------------------ #
# Operational suite
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_operational_good_server_no_harness_error():
    """
    operational suite against good_server: no harness errors (exit != 2).
    Some adversarial tests (e.g. binary_garbage) may legitimately fail on a
    good server that doesn't recover from non-UTF-8 input — that is a server
    limitation, not a harness bug.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--suite", "operational", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    op = report["suites"].get("operational", {})
    summary = op.get("summary", {})

    assert summary.get("errored", 0) == 0, (
        f"operational suite should not have harness errors on good server: {op}"
    )
    assert exit_code != 2, "Harness itself should not error (exit 2) on good server"


@pytest.mark.asyncio
async def test_operational_report_structure():
    """operational suite report has correct test names."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "operational", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    op = report["suites"].get("operational", {})
    test_names = {t["name"] for t in op.get("tests", [])}

    expected = {
        "partial_json",
        "empty_line",
        "binary_garbage",
        "rapid_sequential_requests",
        "response_time_baseline",
        "concurrent_notifications",
        "unknown_notification",
    }
    assert expected.issubset(test_names), (
        f"Missing operational tests. Got: {test_names}"
    )


# ------------------------------------------------------------------ #
# Injection — env var probes
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_injection_env_var_probes_run():
    """
    injection suite includes env_var tests in the report when string-arg tools exist.
    Against good_server, they should all pass or skip.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    inj = report["suites"].get("injection", {})
    tests = inj.get("tests", [])
    env_tests = [t for t in tests if t["name"].startswith("env_var_")]

    # If the good server has string-arg tools, env_var tests should appear
    # and they should all pass (good server doesn't expand env vars)
    for t in env_tests:
        assert t["status"] in ("pass", "warn"), (
            f"env_var test {t['name']!r} should pass or warn on good server, "
            f"got {t['status']!r}: {t['detail']}"
        )

    assert exit_code in (0, 1)


# ------------------------------------------------------------------ #
# Validation — new tests
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_validation_includes_new_tests():
    """validation suite includes deeply_nested_object and array_boundary tests."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    val = report["suites"].get("validation", {})
    test_names = {t["name"] for t in val.get("tests", [])}

    assert "deeply_nested_object" in test_names, (
        f"deeply_nested_object not in validation tests: {test_names}"
    )
    assert "array_boundary" in test_names, (
        f"array_boundary not in validation tests: {test_names}"
    )


@pytest.mark.asyncio
async def test_validation_new_tests_good_server_pass():
    """deeply_nested_object and array_boundary pass on good_server (no crash)."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    val = report["suites"].get("validation", {})
    tests_by_name = {t["name"]: t for t in val.get("tests", [])}

    for test_name in ("deeply_nested_object", "array_boundary"):
        t = tests_by_name.get(test_name)
        if t is None:
            continue  # skip if not present (e.g., no tools)
        assert t["status"] in ("pass", "skip", "warn"), (
            f"{test_name} should pass/skip/warn on good server, "
            f"got {t['status']!r}: {t['detail']}"
        )
