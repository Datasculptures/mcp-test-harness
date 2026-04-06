"""
Report collector: aggregates suite results into a structured report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mcp_test_harness import __version__
from mcp_test_harness.report.scoring import calculate_score, score_to_grade
from mcp_test_harness.suites.base import TestResult


@dataclass
class SuiteReport:
    name: str
    tests: list[TestResult]

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.status == "pass")

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if t.status == "fail")

    @property
    def warned(self) -> int:
        return sum(1 for t in self.tests if t.status == "warn")

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tests if t.status == "skip")

    @property
    def errored(self) -> int:
        return sum(1 for t in self.tests if t.status == "error")


class ReportCollector:
    def __init__(self) -> None:
        self._suites: list[SuiteReport] = []
        self._server_info: dict = {}
        self._transport: str = "stdio"
        self._timestamp: str = datetime.now(timezone.utc).isoformat()

    def add_suite(self, report: SuiteReport) -> None:
        self._suites.append(report)

    def set_server_info(self, info: dict) -> None:
        """Set from InitializeResult.serverInfo."""
        self._server_info = info

    def set_transport(self, transport: str) -> None:
        self._transport = transport

    def to_dict(self) -> dict:
        score = calculate_score(self)
        grade, grade_label = score_to_grade(score)
        return {
            "harness_version": __version__,
            "spec_version": "2025-11-25",
            "server_info": self._server_info,
            "transport": self._transport,
            "timestamp": self._timestamp,
            "score": score,
            "grade": grade,
            "grade_label": grade_label,
            "suites": {
                suite.name: {
                    "tests": [
                        {
                            "name": t.name,
                            "status": t.status,
                            "detail": t.detail,
                            "duration_ms": round(t.duration_ms, 2),
                        }
                        for t in suite.tests
                    ],
                    "summary": {
                        "passed": suite.passed,
                        "failed": suite.failed,
                        "warned": suite.warned,
                        "skipped": suite.skipped,
                        "errored": suite.errored,
                        "total": len(suite.tests),
                    },
                }
                for suite in self._suites
            },
            "totals": {
                "passed": sum(s.passed for s in self._suites),
                "failed": sum(s.failed for s in self._suites),
                "warned": sum(s.warned for s in self._suites),
                "skipped": sum(s.skipped for s in self._suites),
                "errored": sum(s.errored for s in self._suites),
                "total": sum(len(s.tests) for s in self._suites),
            },
        }

    @property
    def exit_code(self) -> int:
        """0 = no failures/errors, 1 = failures detected, 2 = harness errors."""
        has_errors = any(s.errored > 0 for s in self._suites)
        has_failures = any(s.failed > 0 for s in self._suites)
        if has_errors:
            return 2
        if has_failures:
            return 1
        return 0
