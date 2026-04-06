"""
Initialization lifecycle test suite.

All tests use the raw client for precise control over message ordering and timing.
Covers: initialize handshake, version negotiation, ping, stdout purity,
pre-init request rejection, and clean shutdown.
"""

from __future__ import annotations

import asyncio
import json
import time


from mcp_test_harness.client.protocol import (
    MCP_PROTOCOL_VERSION,
    get_error_code,
    is_valid_jsonrpc,
    make_initialize_request,
    make_initialized_notification,
    make_notification,
    make_request,
)
from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult


class InitializationSuite(BaseSuite):
    name = "initialization"

    def __init__(self) -> None:
        self.server_info: dict = {}

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results = []
        tests = [
            self._test_initialize_response_valid,
            self._test_version_negotiation,
            self._test_initialized_notification_accepted,
            self._test_ping_before_initialized,
            self._test_ping_during_operation,
            self._test_stdout_purity,
            self._test_pre_init_request_rejected,
            self._test_shutdown_clean,
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

    async def _test_initialize_response_valid(self, config: ServerConfig) -> TestResult:
        name = "initialize_response_valid"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            req = make_initialize_request(id=1)
            response = await client.send_request(
                req["method"], req.get("params"), id=req["id"]
            )
            if "error" in response:
                return self._fail(name, f"Server returned error: {response['error']}")

            result = response.get("result", {})
            missing = [f for f in ("protocolVersion", "capabilities", "serverInfo") if f not in result]
            if missing:
                return self._fail(name, f"Missing required fields: {missing}")

            if not isinstance(result["capabilities"], dict):
                return self._fail(name, "capabilities is not a dict")

            server_info = result["serverInfo"]
            if not isinstance(server_info, dict):
                return self._fail(name, "serverInfo is not a dict")
            if "name" not in server_info or "version" not in server_info:
                return self._fail(name, f"serverInfo missing name/version: {server_info}")

            # Capture server_info for the report header
            self.server_info = {
                "name": server_info.get("name", ""),
                "version": server_info.get("version", ""),
            }

        return self._pass(name)

    async def _test_version_negotiation(self, config: ServerConfig) -> TestResult:
        name = "version_negotiation"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            req = make_initialize_request(id=1)
            response = await client.send_request(
                req["method"], req.get("params"), id=req["id"]
            )
            if "error" in response:
                return self._fail(name, f"Server returned error during init: {response['error']}")

            result = response.get("result", {})
            server_version = result.get("protocolVersion")
            if server_version is None:
                return self._fail(name, "InitializeResult missing protocolVersion")

            if server_version == MCP_PROTOCOL_VERSION:
                return self._pass(name, f"Server matched version {server_version}")
            else:
                # Server negotiated a different version — allowed by spec, log as observation
                return self._warn(
                    name,
                    f"Server responded with version {server_version!r} "
                    f"(we sent {MCP_PROTOCOL_VERSION!r}). "
                    "Negotiation is allowed but tests will run against this version.",
                )

    async def _test_initialized_notification_accepted(self, config: ServerConfig) -> TestResult:
        name = "initialized_notification_accepted"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            req = make_initialize_request(id=1)
            response = await client.send_request(
                req["method"], req.get("params"), id=req["id"]
            )
            if "error" in response:
                return self._fail(name, "initialize failed, cannot test initialized notification")

            notif = make_initialized_notification()
            await client.send_notification(notif["method"])

            # Notifications must not receive a response.
            # A 1-second timeout with no data = pass.
            try:
                unexpected = await client.read_message(timeout=1.0)
                # A server-initiated message (e.g. logging) is acceptable;
                # only fail if it's an error response matching id=None (shouldn't happen)
                if "error" in unexpected and unexpected.get("id") is None:
                    return self._fail(name, f"Server sent unsolicited error after initialized: {unexpected}")
            except ReadTimeout:
                pass  # expected — no response to notification

        return self._pass(name)

    async def _test_ping_before_initialized(self, config: ServerConfig) -> TestResult:
        name = "ping_before_initialized"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            # Send initialize, get response, but do NOT send initialized notification yet
            req = make_initialize_request(id=1)
            init_response = await client.send_request(
                req["method"], req.get("params"), id=req["id"]
            )
            if "error" in init_response:
                return self._fail(name, "initialize failed, cannot test ping")

            # Ping is explicitly allowed before initialized per spec
            ping_response = await client.send_request("ping", id=2)

            if "error" in ping_response:
                return self._fail(name, f"ping returned error before initialized: {ping_response['error']}")

            result = ping_response.get("result")
            if result != {}:
                return self._warn(
                    name,
                    f"ping result should be {{}} but got {result!r}",
                )

        return self._pass(name)

    async def _test_ping_during_operation(self, config: ServerConfig) -> TestResult:
        name = "ping_during_operation"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()

            ping_response = await client.send_request("ping", id=99)

            if "error" in ping_response:
                return self._fail(name, f"ping returned error: {ping_response['error']}")

            result = ping_response.get("result")
            if result != {}:
                return self._warn(name, f"ping result should be {{}} but got {result!r}")

        return self._pass(name)

    async def _test_stdout_purity(self, config: ServerConfig) -> TestResult:
        name = "stdout_purity"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()

            # Trigger some output
            try:
                await client.send_request("tools/list", id=10)
            except Exception:
                pass  # server may not have tools; we just want stdout lines

            # Brief pause to collect any deferred output
            await asyncio.sleep(0.2)

        # Examine all captured stdout lines
        non_json_lines = []
        for line in client.all_stdout_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                non_json_lines.append(stripped)

        if non_json_lines:
            sample = non_json_lines[0][:120]
            return self._fail(
                name,
                f"{len(non_json_lines)} non-JSON line(s) on stdout. First: {sample!r}",
            )

        return self._pass(name)

    async def _test_pre_init_request_rejected(self, config: ServerConfig) -> TestResult:
        name = "pre_init_request_rejected"
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            # Send tools/list BEFORE initialize
            try:
                response = await client.send_request("tools/list", id=1)
            except ReadTimeout:
                # No response to a pre-init request is acceptable
                return self._pass(name, "Server did not respond to pre-init request (acceptable)")

            if "error" in response:
                return self._pass(name, f"Server correctly returned error code {get_error_code(response)}")

            # A successful tools/list before init is a conformance violation
            return self._fail(
                name,
                "Server responded successfully to tools/list before initialization completed",
            )

    async def _test_shutdown_clean(self, config: ServerConfig) -> TestResult:
        name = "shutdown_clean"
        client = StdioRawClient(config.command, env=config.env, timeout=config.timeout)
        await client.start()
        await client.initialize()

        # Close stdin (the MCP-specified shutdown signal)
        if client._process and client._process.stdin:
            client._process.stdin.close()

        # Wait up to 5 seconds for the process to exit on its own
        deadline = 5.0
        t0 = time.monotonic()
        while (time.monotonic() - t0) < deadline:
            if client.returncode is not None:
                elapsed = (time.monotonic() - t0) * 1000
                await client.stop()  # cleanup reader task
                return self._pass(name, f"Server exited cleanly in {elapsed:.0f}ms")
            await asyncio.sleep(0.1)

        # Didn't exit gracefully — force stop
        await client.stop()
        return self._warn(
            name,
            f"Server did not exit within {deadline}s after stdin closed; required SIGTERM/kill",
        )
