"""
Integration tests: run the full CLI end-to-end against the test fixtures.

Async tests call _run() directly (avoids asyncio.run() inside a running loop).
Sync tests (no server I/O) call main() normally.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from mcp_test_harness import __version__
from mcp_test_harness.cli import _run, main, parse_args
from tests.conftest import GOOD_SERVER_PATH as GOOD_SERVER, BAD_SERVER_PATH as BAD_SERVER

PYTHON = sys.executable


def _args(argv: list[str]):
    return parse_args(argv)


# ------------------------------------------------------------------ #
# Good server — text output
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_full_run_good_server_text():
    """
    Full run produces text output and exits without a harness crash.
    Exit 0 (all pass) or 1 (conformance gaps detected). Never 2 (harness error).
    """
    exit_code = await _run(_args(["stdio", "--", PYTHON, str(GOOD_SERVER)]))
    assert exit_code in (0, 1), f"Harness error (exit 2) on good server"


# ------------------------------------------------------------------ #
# Good server — JSON output
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_full_run_good_server_json():
    """Full run produces valid JSON with the expected top-level schema."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--format", "json", "-o", path,
        "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))

    assert exit_code in (0, 1), "Harness error (exit 2) on good server"

    # Top-level keys
    assert "suites" in report
    assert "totals" in report
    assert "server_info" in report
    assert "harness_version" in report
    assert "spec_version" in report

    # Values
    assert report["harness_version"] == __version__
    assert report["spec_version"] == "2025-11-25"
    assert report["server_info"].get("name"), "server_info.name should be populated"

    # Suite structure
    for suite_name, suite_data in report["suites"].items():
        assert "tests" in suite_data, f"{suite_name} missing 'tests'"
        assert "summary" in suite_data, f"{suite_name} missing 'summary'"
        for key in ("passed", "failed", "warned", "skipped", "errored", "total"):
            assert key in suite_data["summary"], f"{suite_name}.summary missing {key!r}"

    # Totals cross-check
    suite_total = sum(s["summary"]["total"] for s in report["suites"].values())
    assert report["totals"]["total"] == suite_total


# ------------------------------------------------------------------ #
# Bad server — failures detected
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_full_run_bad_server_detects_failures():
    """
    Bad server should cause the harness to detect failures or warnings.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--format", "json", "-o", path,
        "--", PYTHON, str(BAD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))

    assert (
        exit_code in (1, 2)
        or report["totals"]["warned"] > 0
        or report["totals"]["failed"] > 0
    ), "Bad server should have triggered failures or warnings"


# ------------------------------------------------------------------ #
# Suite selection
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_suite_selection_conformance_only():
    """--suite conformance excludes injection and validation suites."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "conformance", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    suite_names = set(report["suites"].keys())

    assert "injection" not in suite_names
    assert "validation" not in suite_names
    assert "initialization" in suite_names
    assert "tools" in suite_names
    assert "errors" in suite_names


@pytest.mark.asyncio
async def test_suite_selection_security_only():
    """--suite security excludes conformance suites."""
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
    assert "initialization" not in suite_names
    assert "tools" not in suite_names


# ------------------------------------------------------------------ #
# Error handling — sync (no running event loop)
# ------------------------------------------------------------------ #

def test_no_command_returns_error():
    """Missing server command → exit code 2."""
    exit_code = main(["stdio"])
    assert exit_code == 2


def test_no_command_with_separator_returns_error():
    """'--' with nothing after → exit code 2."""
    exit_code = main(["stdio", "--"])
    assert exit_code == 2


# ------------------------------------------------------------------ #
# Verbose flag
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_verbose_flag_runs_without_error():
    """--verbose completes without harness error."""
    exit_code = await _run(_args([
        "stdio", "--suite", "conformance", "--verbose",
        "--", PYTHON, str(GOOD_SERVER),
    ]))
    assert exit_code in (0, 1)
