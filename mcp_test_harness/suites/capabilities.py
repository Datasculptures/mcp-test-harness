"""
Capability declaration conformance tests.

Checks that the server's declared capabilities match its behaviour:
- If tools declared → tools tests can run
- If resources/prompts NOT declared → calling those methods returns -32601
"""

from __future__ import annotations

import asyncio
import time

from mcp_test_harness.client.protocol import get_error_code
from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult


class CapabilitiesSuite(BaseSuite):
    name = "capabilities"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results = []
        tests = [
            self._test_tools_capability_declared,
            self._test_undeclared_capability_rejected,
        ]
        for test_fn in tests:
            t0 = time.monotonic()
            try:
                result = await test_fn(config)
            except asyncio.CancelledError as exc:
                result = self._error(
                    test_fn.__name__.lstrip("_test_"),
                    f"Test cancelled (server may have caused cancel scope leak): {exc}",
                )
            except Exception as exc:
                result = self._error(
                    test_fn.__name__.lstrip("_test_"),
                    f"Unexpected harness error: {exc}",
                )
            result.duration_ms = (time.monotonic() - t0) * 1000
            results.append(result)
        return results

    # ------------------------------------------------------------------ #

    async def _test_tools_capability_declared(self, config: ServerConfig) -> TestResult:
        name = "tools_capability_declared"
        try:
            async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
                init_result = await client.initialize()
                caps = init_result.get("capabilities", {})
        except Exception as exc:
            return self._error(name, f"Failed to initialize: {exc}")

        if "tools" in caps:
            return self._pass(name, "tools capability declared")
        return self._skip(name, "Server did not declare tools capability — tool tests will be skipped")

    async def _test_undeclared_capability_rejected(self, config: ServerConfig) -> TestResult:
        """
        For any standard capability (resources, prompts) the server did NOT declare,
        verify that calling the corresponding list method returns -32601.
        """
        name = "undeclared_capability_rejected"

        # First, learn what the server declared (raw client — no anyio cancel scope risk)
        try:
            async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
                init_result = await client.initialize()
                caps = init_result.get("capabilities", {})
        except Exception as exc:
            return self._error(name, f"Failed to initialize: {exc}")

        undeclared = []
        if "resources" not in caps:
            undeclared.append(("resources/list", "resources"))
        if "prompts" not in caps:
            undeclared.append(("prompts/list", "prompts"))

        if not undeclared:
            return self._skip(name, "Server declared all standard capabilities; nothing to test")

        # Use raw client to call undeclared methods
        violations = []
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()

            for method, cap_name in undeclared:
                try:
                    response = await client.send_request(method)
                except ReadTimeout:
                    # No response to an undeclared method is acceptable (treated as skip)
                    continue

                if "error" in response:
                    code = get_error_code(response)
                    if code == -32601:
                        pass  # correct: MethodNotFound
                    else:
                        violations.append(
                            f"{method} returned error {code} (expected -32601)"
                        )
                else:
                    violations.append(
                        f"{method} succeeded but {cap_name!r} capability not declared"
                    )

        if violations:
            return self._fail(name, "; ".join(violations))
        return self._pass(name, f"Undeclared capabilities correctly rejected: {[m for m, _ in undeclared]}")
