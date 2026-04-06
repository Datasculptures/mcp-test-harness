"""
JSON-RPC error handling conformance tests.

All tests use the raw client — we need precise control over malformed
and edge-case messages that a correct SDK client would never send.

Tests verify:
  - Parse error (-32700) for invalid JSON
  - Invalid request (-32600) for structurally invalid JSON-RPC
  - Null id rejection (-32600) — MCP prohibits null ids unlike base JSON-RPC
  - Method not found (-32601)
  - Response id matches request id
  - Response has exactly one of result/error
"""

from __future__ import annotations

import asyncio
import time

from mcp_test_harness.client.protocol import get_error_code
from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult


class ErrorsSuite(BaseSuite):
    name = "errors"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results = []
        tests = [
            self._test_parse_error,
            self._test_invalid_request,
            self._test_null_id_rejected,
            self._test_method_not_found,
            self._test_response_id_matches_request,
            self._test_response_has_result_xor_error,
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

    async def _test_parse_error(self, config: ServerConfig) -> TestResult:
        """Send invalid JSON — expect error code -32700."""
        name = "parse_error"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            await client.send_raw(b"{invalid json\n")

            # Read up to 3 messages, skipping server-initiated notifications
            response = None
            for _ in range(3):
                try:
                    msg = await client.read_message(timeout=config.timeout)
                except ReadTimeout:
                    break
                # Skip server-initiated notifications (have "method" but no "id")
                if "method" in msg and "id" not in msg:
                    continue
                response = msg
                break

        if response is None:
            return self._fail(name, "Server did not respond to invalid JSON (expected -32700)")

        if "error" not in response:
            return self._fail(name, f"Server returned non-error to invalid JSON: {response}")

        code = get_error_code(response)
        if code == -32700:
            return self._pass(name, "Server returned -32700 for invalid JSON")
        return self._warn(
            name,
            f"Server returned error {code} for invalid JSON (expected -32700)",
        )

    async def _test_invalid_request(self, config: ServerConfig) -> TestResult:
        """Send JSON-RPC request missing the 'jsonrpc' field — expect error -32600."""
        name = "invalid_request"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            # Valid JSON but missing "jsonrpc" field
            await client.send_raw(b'{"id": 99, "method": "ping"}\n')

            response = None
            for _ in range(3):
                try:
                    msg = await client.read_message(timeout=config.timeout)
                except ReadTimeout:
                    break
                if "method" in msg and "id" not in msg:
                    continue
                response = msg
                break

        if response is None:
            return self._fail(
                name, "Server did not respond to request missing 'jsonrpc' field (expected -32600)"
            )

        if "error" not in response:
            return self._fail(
                name, f"Server accepted request without 'jsonrpc' field: {response}"
            )

        code = get_error_code(response)
        if code == -32600:
            return self._pass(name, "Server returned -32600 for missing 'jsonrpc' field")
        return self._warn(
            name,
            f"Server returned error {code} for missing 'jsonrpc' (expected -32600)",
        )

    async def _test_null_id_rejected(self, config: ServerConfig) -> TestResult:
        """
        MCP prohibits null request ids (unlike base JSON-RPC which allows them for notifications).
        Send {"jsonrpc":"2.0","id":null,"method":"ping"} — expect error -32600.
        """
        name = "null_id_rejected"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            await client.send_raw(b'{"jsonrpc":"2.0","id":null,"method":"ping"}\n')

            # Null-id responses won't match any pending request, so use read_message().
            # Skip server-initiated notifications (method present, no id).
            response = None
            for _ in range(3):
                try:
                    msg = await client.read_message(timeout=config.timeout)
                except ReadTimeout:
                    break
                if "method" in msg and "id" not in msg:
                    continue
                response = msg
                break

        if response is None:
            return self._fail(
                name, "Server did not respond to request with null id (expected -32600)"
            )

        if "error" not in response:
            return self._fail(
                name,
                f"Server accepted request with null id (MCP prohibits null ids): {response}",
            )

        code = get_error_code(response)
        if code == -32600:
            return self._pass(name, "Server returned -32600 for null id")
        return self._warn(
            name,
            f"Server returned error {code} for null id (expected -32600)",
        )

    async def _test_method_not_found(self, config: ServerConfig) -> TestResult:
        """Send request for nonexistent method — expect error -32601."""
        name = "method_not_found"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            try:
                response = await client.send_request("nonexistent/harness_test_method")
            except ReadTimeout:
                return self._fail(name, "Server did not respond to unknown method (expected -32601)")

        if "error" not in response:
            return self._fail(name, f"Server returned success for unknown method: {response}")

        code = get_error_code(response)
        if code == -32601:
            return self._pass(name, "Server returned -32601 for unknown method")
        return self._warn(
            name,
            f"Server returned error {code} for unknown method (expected -32601)",
        )

    async def _test_response_id_matches_request(self, config: ServerConfig) -> TestResult:
        """Send request with id=42, verify response id is 42."""
        name = "response_id_matches_request"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            try:
                response = await client.send_request("ping", id=42)
            except ReadTimeout:
                return self._fail(name, "Server did not respond to ping with id=42")

        resp_id = response.get("id")
        if resp_id != 42:
            return self._fail(
                name,
                f"Response id {resp_id!r} does not match request id 42",
            )
        return self._pass(name, "Response id matches request id (42)")

    async def _test_response_has_result_xor_error(self, config: ServerConfig) -> TestResult:
        """
        A valid JSON-RPC response has exactly one of 'result' or 'error', not both, not neither.
        Test with a valid request (ping) and an invalid one (unknown method).
        """
        name = "response_has_result_xor_error"
        violations = []

        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()

            # Valid request: should have result
            try:
                ping_resp = await client.send_request("ping")
            except ReadTimeout:
                return self._error(name, "Server did not respond to ping")

            has_result = "result" in ping_resp
            has_error = "error" in ping_resp
            if has_result and has_error:
                violations.append("ping response has both 'result' and 'error'")
            elif not has_result and not has_error:
                violations.append("ping response has neither 'result' nor 'error'")

            # Invalid request: should have error
            try:
                err_resp = await client.send_request("nonexistent/harness_xor_test")
            except ReadTimeout:
                return self._error(name, "Server did not respond to unknown method")

            has_result = "result" in err_resp
            has_error = "error" in err_resp
            if has_result and has_error:
                violations.append("unknown-method response has both 'result' and 'error'")
            elif not has_result and not has_error:
                violations.append("unknown-method response has neither 'result' nor 'error'")

        if violations:
            return self._fail(name, "; ".join(violations))
        return self._pass(name, "All responses have exactly one of result/error")
