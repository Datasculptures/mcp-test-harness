"""Unit tests for the scoring system."""

from __future__ import annotations

from mcp_test_harness.report.collector import ReportCollector, SuiteReport
from mcp_test_harness.report.scoring import (
    calculate_score,
    score_to_grade,
    score_to_badge_colour,
)
from mcp_test_harness.suites.base import TestResult


def _make_collector(*suites: tuple[str, list[TestResult]]) -> ReportCollector:
    c = ReportCollector()
    for name, tests in suites:
        c.add_suite(SuiteReport(name=name, tests=tests))
    return c


def _t(status: str) -> TestResult:
    return TestResult(name="t", status=status, detail="")


# ------------------------------------------------------------------ #
# calculate_score
# ------------------------------------------------------------------ #

def test_perfect_score():
    c = _make_collector(
        ("initialization", [_t("pass")] * 5),
        ("injection",      [_t("pass")] * 5),
        ("operational",    [_t("pass")] * 5),
    )
    assert calculate_score(c) == 100


def test_conformance_failure_deduction():
    c = _make_collector(("initialization", [_t("fail")]))
    assert calculate_score(c) == 95


def test_security_failure_deduction():
    c = _make_collector(("injection", [_t("fail")]))
    assert calculate_score(c) == 92


def test_security_warning_deduction():
    c = _make_collector(("validation", [_t("warn")]))
    assert calculate_score(c) == 98


def test_operational_failure_deduction():
    c = _make_collector(("operational", [_t("fail")]))
    assert calculate_score(c) == 97


def test_operational_warning_deduction():
    c = _make_collector(("operational", [_t("warn")]))
    assert calculate_score(c) == 99


def test_multiple_deductions():
    # 3 conformance failures (15) + 2 security failures (16) + 1 security warning (2) = 33
    c = _make_collector(
        ("initialization", [_t("fail"), _t("fail"), _t("fail")]),
        ("injection",      [_t("fail"), _t("fail"), _t("warn")]),
    )
    assert calculate_score(c) == 67


def test_score_floor_zero():
    # 20 security failures = 160 deductions, floor at 0
    c = _make_collector(("injection", [_t("fail")] * 20))
    assert calculate_score(c) == 0


def test_skips_no_penalty():
    c = _make_collector(("injection", [_t("skip")] * 10))
    assert calculate_score(c) == 100


def test_errors_no_penalty():
    c = _make_collector(("injection", [_t("error")] * 10))
    assert calculate_score(c) == 100


# ------------------------------------------------------------------ #
# score_to_grade
# ------------------------------------------------------------------ #

def test_grade_a():
    assert score_to_grade(100) == ("A", "Excellent")
    assert score_to_grade(90)  == ("A", "Excellent")


def test_grade_b():
    assert score_to_grade(89) == ("B", "Good")
    assert score_to_grade(75) == ("B", "Good")


def test_grade_c():
    assert score_to_grade(74) == ("C", "Acceptable")
    assert score_to_grade(60) == ("C", "Acceptable")


def test_grade_d():
    assert score_to_grade(59) == ("D", "Poor")
    assert score_to_grade(40) == ("D", "Poor")


def test_grade_f():
    assert score_to_grade(39) == ("F", "Failing")
    assert score_to_grade(0)  == ("F", "Failing")


# ------------------------------------------------------------------ #
# score_to_badge_colour
# ------------------------------------------------------------------ #

def test_badge_colour_a():
    assert score_to_badge_colour(100) == "brightgreen"
    assert score_to_badge_colour(90)  == "brightgreen"


def test_badge_colour_b():
    assert score_to_badge_colour(89) == "green"
    assert score_to_badge_colour(75) == "green"


def test_badge_colour_c():
    assert score_to_badge_colour(74) == "yellow"


def test_badge_colour_d():
    assert score_to_badge_colour(59) == "orange"


def test_badge_colour_f():
    assert score_to_badge_colour(0) == "red"


# ------------------------------------------------------------------ #
# Score in collector.to_dict()
# ------------------------------------------------------------------ #

def test_score_in_to_dict():
    c = _make_collector(("initialization", [_t("pass")]))
    d = c.to_dict()
    assert "score" in d
    assert "grade" in d
    assert "grade_label" in d
    assert d["score"] == 100
    assert d["grade"] == "A"
    assert d["grade_label"] == "Excellent"
