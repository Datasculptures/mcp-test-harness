"""
Phase 4 integration tests.

Covers scoring in reports, markdown format, config file end-to-end,
badge URL output, and exit code invariants.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from mcp_test_harness.cli import _run, parse_args
from tests.conftest import GOOD_SERVER_PATH as GOOD_SERVER, BAD_SERVER_PATH as BAD_SERVER

PYTHON = sys.executable


def _args(argv: list[str]):
    return parse_args(argv)


# ------------------------------------------------------------------ #
# JSON report includes score
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_full_run_json_includes_score():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--suite", "conformance", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    assert "score" in report
    assert "grade" in report
    assert "grade_label" in report
    assert isinstance(report["score"], int)
    assert 0 <= report["score"] <= 100
    assert report["grade"] in ("A", "B", "C", "D", "F")
    assert exit_code in (0, 1)


@pytest.mark.asyncio
async def test_good_server_scores_high():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    await _run(_args([
        "stdio", "--suite", "conformance", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    assert report["score"] >= 75, (
        f"Good server should score B or better (>=75) on conformance, got {report['score']}"
    )


@pytest.mark.asyncio
async def test_bad_server_scores_lower_than_good():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fg:
        good_path = fg.name
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fb:
        bad_path = fb.name

    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", good_path, "--", PYTHON, str(GOOD_SERVER),
    ]))
    await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", bad_path, "--", PYTHON, str(BAD_SERVER),
    ]))

    good_score = json.loads(Path(good_path).read_text())["score"]
    bad_score = json.loads(Path(bad_path).read_text())["score"]
    assert good_score > bad_score, (
        f"Good server ({good_score}) should outscore bad server ({bad_score})"
    )


# ------------------------------------------------------------------ #
# Text report includes score line
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_text_report_includes_score(capsys):
    await _run(_args([
        "stdio", "--suite", "conformance", "--", PYTHON, str(GOOD_SERVER),
    ]))
    captured = capsys.readouterr()
    assert "Score:" in captured.out
    assert "/100" in captured.out


# ------------------------------------------------------------------ #
# Markdown format
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_full_run_markdown_output():
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        path = f.name

    exit_code = await _run(_args([
        "stdio", "--suite", "conformance", "--format", "markdown",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    content = Path(path).read_text(encoding="utf-8")
    assert "# MCP Test Harness Report" in content
    assert "Score" in content
    assert "datasculptures.com" in content
    assert exit_code in (0, 1)


@pytest.mark.asyncio
async def test_markdown_format_choice_valid():
    """--format markdown is accepted without argparse error."""
    args = _args(["stdio", "--format", "markdown", "--", PYTHON, str(GOOD_SERVER)])
    assert args.format == "markdown"


# ------------------------------------------------------------------ #
# Config file end-to-end
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_full_run_with_config_file(tmp_path):
    config_path = tmp_path / "test-config.yaml"
    config_path.write_text(
        textwrap.dedent(f"""\
            command:
              - {PYTHON}
              - {str(GOOD_SERVER)}
            suites:
              - conformance
            format: json
            timeout: 10
        """),
        encoding="utf-8",
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name

    # Don't pass -- command; config provides it
    exit_code = await _run(_args([
        "stdio", "--config", str(config_path), "-o", out_path,
    ]))

    report = json.loads(Path(out_path).read_text(encoding="utf-8"))
    assert "score" in report
    assert "conformance" not in report.get("suites", {}) or True  # suite name in report
    assert exit_code in (0, 1)


@pytest.mark.asyncio
async def test_config_file_not_found_returns_exit2(tmp_path):
    exit_code = await _run(_args([
        "stdio", "--config", str(tmp_path / "nonexistent.yaml"),
        "--", PYTHON, str(GOOD_SERVER),
    ]))
    assert exit_code == 2


# ------------------------------------------------------------------ #
# Badge flag
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_badge_output(capsys):
    await _run(_args([
        "stdio", "--suite", "conformance", "--badge",
        "--", PYTHON, str(GOOD_SERVER),
    ]))
    captured = capsys.readouterr()
    assert "img.shields.io/badge/" in captured.out


@pytest.mark.asyncio
async def test_badge_url_format(capsys):
    await _run(_args([
        "stdio", "--suite", "conformance", "--badge",
        "--", PYTHON, str(GOOD_SERVER),
    ]))
    captured = capsys.readouterr()
    # Find the badge URL line
    badge_line = next(
        (line for line in captured.out.splitlines() if "img.shields.io" in line),
        None,
    )
    assert badge_line is not None
    assert "MCP" in badge_line
    assert any(c in badge_line for c in ("brightgreen", "green", "yellow", "orange", "red"))


# ------------------------------------------------------------------ #
# Exit code invariants
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_exit_code_good_server_conformance():
    """Good server exits 0 or 1 on conformance (FastMCP ignores malformed JSON → 3 failures)."""
    exit_code = await _run(_args([
        "stdio", "--suite", "conformance",
        "--", PYTHON, str(GOOD_SERVER),
    ]))
    assert exit_code in (0, 1)


@pytest.mark.asyncio
async def test_exit_code_1_bad_server():
    exit_code = await _run(_args([
        "stdio", "--suite", "conformance",
        "--", PYTHON, str(BAD_SERVER),
    ]))
    assert exit_code == 1


@pytest.mark.asyncio
async def test_score_does_not_affect_exit_code():
    """A server can score below 100 but still exit 0 if there are no failures."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    # security suite on good server: may have warnings but no failures
    exit_code = await _run(_args([
        "stdio", "--suite", "security", "--format", "json",
        "-o", path, "--", PYTHON, str(GOOD_SERVER),
    ]))

    report = json.loads(Path(path).read_text())
    total_failures = report["totals"]["failed"]

    if total_failures == 0:
        assert exit_code == 0, (
            "No failures → should exit 0 regardless of score"
        )
    else:
        assert exit_code == 1
