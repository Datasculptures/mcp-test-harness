"""Unit tests for the markdown report formatter."""

from __future__ import annotations

from mcp_test_harness import __version__
from mcp_test_harness.report.collector import ReportCollector, SuiteReport
from mcp_test_harness.report.markdown_report import format_markdown_report
from mcp_test_harness.suites.base import TestResult


def _make_collector(*suites: tuple[str, list[TestResult]]) -> ReportCollector:
    c = ReportCollector()
    for name, tests in suites:
        c.add_suite(SuiteReport(name=name, tests=tests))
    return c


def _t(name: str, status: str, detail: str = "") -> TestResult:
    return TestResult(name=name, status=status, detail=detail)


def test_markdown_has_title():
    c = _make_collector(("initialization", [_t("foo", "pass")]))
    md = format_markdown_report(c)
    assert "# MCP Test Harness Report" in md


def test_markdown_has_header_table():
    c = _make_collector(("initialization", [_t("foo", "pass")]))
    md = format_markdown_report(c)
    assert "| **Server** |" in md
    assert "| **Score** |" in md
    assert "| **Spec** |" in md
    assert "| **Transport** |" in md
    assert "| **Date** |" in md


def test_markdown_has_suite_tables():
    c = _make_collector(
        ("initialization", [_t("version_check", "pass")]),
        ("injection",      [_t("shell_semi",    "pass")]),
    )
    md = format_markdown_report(c)
    assert "| Test | Status | Detail |" in md
    assert "version_check" in md
    assert "shell_semi" in md


def test_markdown_shows_detail_for_warnings():
    c = _make_collector(
        ("injection", [_t("null_byte", "warn", "null byte accepted")])
    )
    md = format_markdown_report(c)
    assert "null byte accepted" in md


def test_markdown_hides_detail_for_passes():
    c = _make_collector(
        ("injection", [_t("safe_test", "pass", "all good")])
    )
    md = format_markdown_report(c)
    # Detail should not appear for a passing test
    assert "all good" not in md


def test_markdown_shows_detail_for_failures():
    c = _make_collector(
        ("injection", [_t("bad_test", "fail", "canary found")])
    )
    md = format_markdown_report(c)
    assert "canary found" in md


def test_markdown_footer():
    c = _make_collector(("initialization", [_t("t", "pass")]))
    md = format_markdown_report(c)
    assert "mcp-test-harness" in md
    assert __version__ in md
    assert "datasculptures.com" in md


def test_markdown_score_in_header():
    c = _make_collector(("initialization", [_t("t", "pass")]))
    md = format_markdown_report(c)
    # Score should be 100/100 with no failures
    assert "100/100" in md
    assert "Excellent" in md


def test_markdown_sections():
    """Suites are grouped into Conformance / Security / Operational sections."""
    c = _make_collector(
        ("initialization", [_t("t", "pass")]),
        ("injection",      [_t("t", "pass")]),
        ("operational",    [_t("t", "pass")]),
    )
    md = format_markdown_report(c)
    assert "### Conformance" in md
    assert "### Security" in md
    assert "### Operational" in md


def test_markdown_summary_section():
    c = _make_collector(("initialization", [_t("t", "pass")]))
    md = format_markdown_report(c)
    assert "## Summary" in md
