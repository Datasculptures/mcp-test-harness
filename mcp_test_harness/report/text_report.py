"""
Human-readable text report formatter.

No ANSI colour codes — plain Unicode symbols only.
Symbol key:  ✓ pass   ✗ fail   ⚠ warn   ○ skip   ! error
"""

from __future__ import annotations

from mcp_test_harness import __version__
from mcp_test_harness.report.collector import ReportCollector, SuiteReport
from mcp_test_harness.report.scoring import calculate_score, score_to_grade
from mcp_test_harness.suites.base import TestResult

_SYMBOLS = {
    "pass":  "✓",
    "fail":  "✗",
    "warn":  "⚠",
    "skip":  "○",
    "error": "!",
}


def _suite_summary(suite: SuiteReport) -> str:
    parts: list[str] = []
    if suite.passed:
        parts.append(f"{suite.passed} passed")
    if suite.failed:
        parts.append(f"{suite.failed} failed")
    if suite.warned:
        parts.append(f"{suite.warned} {'warning' if suite.warned == 1 else 'warnings'}")
    if suite.skipped:
        parts.append(f"{suite.skipped} skipped")
    if suite.errored:
        parts.append(f"{suite.errored} {'error' if suite.errored == 1 else 'errors'}")
    return ", ".join(parts) if parts else "0 tests"


def _totals_line(collector: ReportCollector) -> str:
    d = collector.to_dict()["totals"]
    parts: list[str] = []
    if d["passed"]:
        parts.append(f"{d['passed']} passed")
    if d["failed"]:
        parts.append(f"{d['failed']} failed")
    if d["warned"]:
        parts.append(f"{d['warned']} {'warning' if d['warned'] == 1 else 'warnings'}")
    if d["skipped"]:
        parts.append(f"{d['skipped']} skipped")
    if d["errored"]:
        parts.append(f"{d['errored']} {'error' if d['errored'] == 1 else 'errors'}")
    return ", ".join(parts) if parts else "0 tests"


def format_text_report(collector: ReportCollector, verbose: bool = False) -> str:
    lines: list[str] = []

    # Header
    d = collector.to_dict()
    server = d["server_info"]
    if server:
        server_str = f"{server.get('name', '?')} v{server.get('version', '?')}"
    else:
        server_str = "unknown"

    lines.append(f"MCP Test Harness v{__version__}")
    lines.append(f"Spec: {d['spec_version']}")
    lines.append(f"Server: {server_str}")
    lines.append(f"Transport: {d['transport']}")

    # Suites
    for suite_name, suite_data in d["suites"].items():
        lines.append("")
        lines.append(f"═══ {suite_name} ═══")
        for test in suite_data["tests"]:
            sym = _SYMBOLS.get(test["status"], "?")
            detail = test["detail"]
            show_detail = verbose or test["status"] != "pass"
            if show_detail and detail:
                lines.append(f"  {sym} {test['name']} — {detail}")
            else:
                lines.append(f"  {sym} {test['name']}")

        # Find the corresponding SuiteReport for summary
        suite_report = next(
            (s for s in collector._suites if s.name == suite_name), None
        )
        if suite_report is not None:
            lines.append(f"  {_suite_summary(suite_report)}")

    # Footer
    score = calculate_score(collector)
    grade, grade_label = score_to_grade(score)
    lines.append("")
    lines.append("─" * 40)
    lines.append(f"Total: {_totals_line(collector)}")
    lines.append(f"Score: {score}/100 ({grade} \u2014 {grade_label})")

    return "\n".join(lines)
