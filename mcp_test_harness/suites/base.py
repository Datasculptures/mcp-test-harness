"""Base classes for test suites and results."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Awaitable

from mcp_test_harness.config import ServerConfig


@dataclass
class TestResult:
    name: str
    status: str
    """One of: 'pass', 'fail', 'skip', 'warn', 'error'"""
    detail: str = ""
    duration_ms: float = 0.0

    def __str__(self) -> str:
        symbol = {"pass": "✓", "fail": "✗", "warn": "⚠", "skip": "○", "error": "E"}.get(
            self.status, "?"
        )
        line = f"  {symbol} {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        return line


class BaseSuite:
    """Base class for all test suites."""

    name: str = "unnamed"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        """Run all tests in this suite. Returns list of TestResult."""
        raise NotImplementedError

    def _result(
        self,
        name: str,
        status: str,
        detail: str = "",
        duration_ms: float = 0.0,
    ) -> TestResult:
        return TestResult(
            name=name, status=status, detail=detail, duration_ms=duration_ms
        )

    def _pass(self, name: str, detail: str = "", duration_ms: float = 0.0) -> TestResult:
        return self._result(name, "pass", detail, duration_ms)

    def _fail(self, name: str, detail: str = "", duration_ms: float = 0.0) -> TestResult:
        return self._result(name, "fail", detail, duration_ms)

    def _warn(self, name: str, detail: str = "", duration_ms: float = 0.0) -> TestResult:
        return self._result(name, "warn", detail, duration_ms)

    def _skip(self, name: str, detail: str = "") -> TestResult:
        return self._result(name, "skip", detail)

    def _error(self, name: str, detail: str = "", duration_ms: float = 0.0) -> TestResult:
        return self._result(name, "error", detail, duration_ms)
