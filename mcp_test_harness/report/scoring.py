"""
Quality scoring for MCP test harness reports.

Score is 0–100, starting at 100 with deductions per finding:
  Conformance failure:   5 points
  Security failure:      8 points
  Security warning:      2 points
  Operational failure:   3 points
  Operational warning:   1 point
  Skip / error:          0 points (not tested or harness issue)

Grade bands:
  90–100  A  Excellent
  75–89   B  Good
  60–74   C  Acceptable
  40–59   D  Poor
   0–39   F  Failing
"""

from __future__ import annotations

# Suite name → category mapping
_SUITE_CATEGORIES: dict[str, str] = {
    "initialization":  "conformance",
    "capabilities":    "conformance",
    "tools":           "conformance",
    "errors":          "conformance",
    "injection":       "security",
    "validation":      "security",
    "path_traversal":  "security",
    "resource_scope":  "security",
    "operational":     "operational",
}

# Deduction weights: (category, status) → points
_WEIGHTS: dict[tuple[str, str], int] = {
    ("conformance",  "fail"): 5,
    ("security",     "fail"): 8,
    ("security",     "warn"): 2,
    ("operational",  "fail"): 3,
    ("operational",  "warn"): 1,
}


def categorize_suite(name: str) -> str:
    """Return category for a suite name. Defaults to 'conformance' for unknowns."""
    return _SUITE_CATEGORIES.get(name, "conformance")


def calculate_score(collector) -> int:
    """
    Calculate quality score 0–100.
    Starts at 100, deducts for failures and warnings per documented weights.
    Score never goes below 0.
    """
    deductions = 0
    for suite in collector._suites:
        category = categorize_suite(suite.name)
        for test in suite.tests:
            deductions += _WEIGHTS.get((category, test.status), 0)
    return max(0, 100 - deductions)


def score_to_grade(score: int) -> tuple[str, str]:
    """Return (letter, label) for a score."""
    if score >= 90:
        return ("A", "Excellent")
    if score >= 75:
        return ("B", "Good")
    if score >= 60:
        return ("C", "Acceptable")
    if score >= 40:
        return ("D", "Poor")
    return ("F", "Failing")


def score_to_badge_colour(score: int) -> str:
    """Return a shields.io colour name for a score."""
    if score >= 90:
        return "brightgreen"
    if score >= 75:
        return "green"
    if score >= 60:
        return "yellow"
    if score >= 40:
        return "orange"
    return "red"
