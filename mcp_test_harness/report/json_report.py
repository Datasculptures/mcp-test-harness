"""JSON report formatter."""

from __future__ import annotations

import json

from mcp_test_harness.report.collector import ReportCollector


def format_json_report(collector: ReportCollector) -> str:
    """Return the report as an indented JSON string."""
    return json.dumps(collector.to_dict(), indent=2)


def write_json_report(collector: ReportCollector, path: str) -> None:
    """Write JSON report to a file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(format_json_report(collector))
