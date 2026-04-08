"""
Tests for the injection suite's filesystem side-effect signal (Option A).

Verifies:
  - True positive: run_command_shell in bad_server actually executes the shell →
    canary file created → harness reports FAIL
  - False positive prevention: tools that echo, normalize, or return modified
    text do NOT create canary files → harness reports PASS
  - Good server tools (echo, greet) all pass the injection tests
  - Canary directory is cleaned up after the suite run
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from mcp_test_harness.cli import _run, parse_args
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.injection import InjectionSuite, _make_canary_payloads
from tests.conftest import GOOD_SERVER_PATH as GOOD_SERVER, BAD_SERVER_PATH as BAD_SERVER

PYTHON = sys.executable


def _args(argv: list[str]):
    return parse_args(argv)


# ------------------------------------------------------------------ #
# Unit tests: _make_canary_payloads
# ------------------------------------------------------------------ #

def test_canary_payloads_count(tmp_path):
    payloads = _make_canary_payloads(tmp_path)
    assert len(payloads) == 5


def test_canary_payloads_names(tmp_path):
    payloads = _make_canary_payloads(tmp_path)
    names = {p[1] for p in payloads}
    assert names == {"semicolon", "pipe", "backtick", "dollar_paren", "ampersand"}


def test_canary_payloads_files_are_distinct(tmp_path):
    payloads = _make_canary_payloads(tmp_path)
    files = [p[2] for p in payloads]
    assert len(files) == len(set(files)), "each payload must have a unique canary file"


def test_canary_file_paths_are_in_canary_dir(tmp_path):
    payloads = _make_canary_payloads(tmp_path)
    for _, _, canary_file in payloads:
        assert canary_file.parent == tmp_path


def test_canary_payloads_contain_file_path(tmp_path):
    """Each payload string must reference its canary file's path."""
    payloads = _make_canary_payloads(tmp_path)
    for payload_str, short_name, canary_file in payloads:
        assert str(canary_file) in payload_str, (
            f"{short_name!r} payload does not contain canary file path"
        )


def test_canary_file_creation_simulated(tmp_path):
    """Manually creating a canary file simulates what a vulnerable server would do."""
    payloads = _make_canary_payloads(tmp_path)
    _, _, canary_file = payloads[0]
    assert not canary_file.exists()
    canary_file.touch()
    assert canary_file.exists()


# ------------------------------------------------------------------ #
# Integration: good server — no false positives
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_injection_good_server_no_failures():
    """
    Good server tools echo or process input without executing shell commands.
    All metachar injection tests should PASS (no canary files created).
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    inj = report["suites"].get("injection", {})
    metachar_tests = [
        t for t in inj.get("tests", [])
        if t["name"].startswith("shell_metachar_")
    ]

    failed = [t for t in metachar_tests if t["status"] == "fail"]
    assert failed == [], (
        f"Good server triggered false-positive injection failures: "
        f"{[t['name'] for t in failed]}"
    )
    assert exit_code in (0, 1)


# ------------------------------------------------------------------ #
# Integration: bad server — true positives via run_command_shell
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_injection_bad_server_detects_shell_execution():
    """
    bad_server's run_command_shell passes its argument to shell=True.
    At least one metachar variant must create a canary file → FAIL.
    The pipe variant works on both Windows (cmd.exe) and Unix (sh).
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(BAD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    inj = report["suites"].get("injection", {})
    metachar_tests = [
        t for t in inj.get("tests", [])
        if t["name"].startswith("shell_metachar_") and "run_command_shell" in t["name"]
    ]

    failed = [t for t in metachar_tests if t["status"] == "fail"]
    assert failed, (
        "Expected at least one shell_metachar failure against run_command_shell, "
        f"got: {[(t['name'], t['status']) for t in metachar_tests]}"
    )
    # Confirm the detail mentions canary file, not the old string-based message
    for t in failed:
        assert "canary file" in t["detail"], (
            f"Failure detail should mention canary file, got: {t['detail']!r}"
        )


@pytest.mark.asyncio
async def test_injection_semicolon_variant_detects_on_both_platforms():
    """
    The semicolon variant creates a canary file on both Windows and Unix:
      Unix sh:     hello; touch "path"    — ';' is an unconditional command separator
      Windows:     hello; echo.>"path"   — cmd.exe treats ';' as a command separator
    Verify it specifically fails against run_command_shell.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(BAD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    inj = report["suites"].get("injection", {})
    semi_test = next(
        (t for t in inj.get("tests", [])
         if t["name"] == "shell_metachar_semicolon__run_command_shell"),
        None,
    )

    assert semi_test is not None, (
        "shell_metachar_semicolon__run_command_shell test not found in injection suite"
    )
    assert semi_test["status"] == "fail", (
        f"semicolon variant should fail against run_command_shell on all platforms, "
        f"got {semi_test['status']!r}: {semi_test['detail']}"
    )


# ------------------------------------------------------------------ #
# Integration: echo_unsafe and run_command do NOT trigger (no shell execution)
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_injection_echo_unsafe_passes():
    """
    echo_unsafe echoes back the full payload (including metachar prefix).
    No shell execution → no canary file → PASS.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(BAD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    inj = report["suites"].get("injection", {})
    echo_tests = [
        t for t in inj.get("tests", [])
        if t["name"].startswith("shell_metachar_") and "echo_unsafe" in t["name"]
    ]

    failed = [t for t in echo_tests if t["status"] == "fail"]
    assert failed == [], (
        f"echo_unsafe should not trigger injection failures (it echoes, doesn't execute): "
        f"{[t['name'] for t in failed]}"
    )


@pytest.mark.asyncio
async def test_injection_run_command_passes():
    """
    run_command simulates injection by returning a modified string — it does NOT
    execute a shell. No canary file created → PASS under the new signal.
    (This was a false positive under the old canary-string signal.)
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(BAD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    inj = report["suites"].get("injection", {})
    rc_tests = [
        t for t in inj.get("tests", [])
        if t["name"].startswith("shell_metachar_") and "__run_command__" in t["name"]
    ]

    failed = [t for t in rc_tests if t["status"] == "fail"]
    assert failed == [], (
        f"run_command should PASS (no shell execution, no canary file): "
        f"{[t['name'] for t in failed]}"
    )


# ------------------------------------------------------------------ #
# Canary directory cleanup
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_canary_dir_cleaned_up_after_run():
    """
    The injection suite must clean up its scratch directory after the run,
    regardless of success or failure.
    """
    # Capture canary dirs created during a run by hooking into tempfile.mkdtemp.
    # Simpler: just verify no mcp_harness_canary_* dirs linger in tempdir.
    import glob

    tmp = tempfile.gettempdir()
    before = set(glob.glob(str(Path(tmp) / "mcp_harness_canary_*")))

    await _run(_args([
        "stdio", "--suite", "security",
        "--", PYTHON, str(GOOD_SERVER),
    ]))

    after = set(glob.glob(str(Path(tmp) / "mcp_harness_canary_*")))
    lingering = after - before
    assert not lingering, (
        f"Canary directories were not cleaned up: {lingering}"
    )
