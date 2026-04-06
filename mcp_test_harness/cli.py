"""
CLI entry point for mcp-test-harness.

Usage:
    mcp-test-harness stdio -- python -m my_server
    mcp-test-harness stdio --suite conformance -- python -m my_server
    mcp-test-harness stdio --suite security --format json -o report.json -- python -m my_server
    mcp-test-harness stdio -v -- python -m my_server
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys

from mcp_test_harness.config import ServerConfig
from mcp_test_harness.config_file import load_config_file, merge_config
from mcp_test_harness.report.collector import ReportCollector, SuiteReport
from mcp_test_harness.report.json_report import format_json_report, write_json_report
from mcp_test_harness.report.scoring import calculate_score, score_to_badge_colour, score_to_grade
from mcp_test_harness.report.markdown_report import format_markdown_report
from mcp_test_harness.report.text_report import format_text_report
from mcp_test_harness.suites.base import TestResult
from mcp_test_harness.suites.capabilities import CapabilitiesSuite
from mcp_test_harness.suites.errors import ErrorsSuite
from mcp_test_harness.suites.initialization import InitializationSuite
from mcp_test_harness.suites.injection import InjectionSuite
from mcp_test_harness.suites.operational import OperationalSuite
from mcp_test_harness.suites.path_traversal import PathTraversalSuite
from mcp_test_harness.suites.resource_scope import ResourceScopeSuite
from mcp_test_harness.suites.tools import ToolsSuite
from mcp_test_harness.suites.validation import ValidationSuite

CONFORMANCE_SUITES = [
    InitializationSuite,
    CapabilitiesSuite,
    ToolsSuite,
    ErrorsSuite,
]

SECURITY_SUITES = [
    InjectionSuite,
    ValidationSuite,
    PathTraversalSuite,
    ResourceScopeSuite,
]

OPERATIONAL_SUITES = [
    OperationalSuite,
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mcp-test-harness",
        description="Security and conformance testing for MCP servers",
    )
    subparsers = parser.add_subparsers(dest="transport", required=True)

    stdio = subparsers.add_parser("stdio", help="Test a STDIO MCP server")
    stdio.add_argument(
        "--suite",
        action="append",
        choices=["conformance", "security", "operational", "all"],
        default=None,
        dest="suite",
        help="Test suites to run (default: all). Can be repeated.",
    )
    stdio.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    stdio.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Write report to file instead of stdout",
    )
    stdio.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show details for all tests, not just failures and warnings",
    )
    stdio.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Config file path (default: .mcp-test-harness.yaml in current directory)",
    )
    stdio.add_argument(
        "--badge",
        action="store_true",
        help="Print a shields.io badge URL for the score",
    )
    stdio.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds (default: 10)",
    )
    stdio.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Server command to test (after --)",
    )

    return parser.parse_args(argv)


def _resolve_suites(suite_args: list[str] | None) -> list:
    if suite_args is None or "all" in suite_args:
        return CONFORMANCE_SUITES + SECURITY_SUITES + OPERATIONAL_SUITES
    suites: list = []
    if "conformance" in suite_args:
        suites.extend(CONFORMANCE_SUITES)
    if "security" in suite_args:
        suites.extend(SECURITY_SUITES)
    if "operational" in suite_args:
        suites.extend(OPERATIONAL_SUITES)
    return suites


async def _run(args: argparse.Namespace) -> int:
    # Load and merge config file (CLI args take precedence)
    try:
        file_config = load_config_file(getattr(args, "config", None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    merge_config(args, file_config)

    # Strip leading "--" separator from command list
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("Error: no server command specified.", file=sys.stderr)
        print("Usage: mcp-test-harness stdio -- <command> [args...]", file=sys.stderr)
        return 2

    config = ServerConfig(
        command=command,
        transport="stdio",
        timeout=args.timeout,
    )

    collector = ReportCollector()
    collector.set_transport("stdio")

    suite_classes = _resolve_suites(args.suite)

    for suite_cls in suite_classes:
        suite = suite_cls()
        try:
            results = await suite.run(config)
        except Exception as exc:
            results = [
                TestResult(
                    name=f"{suite_cls.name}_suite_error",
                    status="error",
                    detail=str(exc),
                )
            ]
        collector.add_suite(SuiteReport(name=suite_cls.name, tests=results))
        # Capture server_info from the first suite that successfully initializes
        if (
            not collector._server_info
            and hasattr(suite, "server_info")
            and suite.server_info
        ):
            collector.set_server_info(suite.server_info)

    # Generate report
    if args.format == "json":
        report_text = format_json_report(collector)
    elif args.format == "markdown":
        report_text = format_markdown_report(collector)
    else:
        report_text = format_text_report(collector, verbose=args.verbose)

    # Output
    if args.output:
        try:
            if args.format == "json":
                write_json_report(collector, args.output)
            else:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(report_text)
        except OSError as exc:
            print(f"Error writing report to {args.output!r}: {exc}", file=sys.stderr)
            return 2
        # Print to stdout as well
        _safe_print(report_text)
    else:
        _safe_print(report_text)

    # Badge URL
    if getattr(args, "badge", False):
        score = calculate_score(collector)
        grade, _ = score_to_grade(score)
        colour = score_to_badge_colour(score)
        label = f"MCP%20Harness"
        message = f"{score}%2F100%20{grade}"
        badge_url = f"https://img.shields.io/badge/{label}-{message}-{colour}"
        _safe_print(badge_url)

    return collector.exit_code


def _safe_print(text: str) -> None:
    """Print text to stdout, recoding to UTF-8 if the terminal can't handle Unicode."""
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))
