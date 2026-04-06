"""
Operational reliability test suite.

Tests how the server handles real-world stress: malformed messages,
partial JSON, binary garbage, rapid sequential requests, and large payloads.

Uses a single server process for all tests (restarting per-test would be too
slow). If the server becomes unresponsive mid-suite, remaining tests are skipped.

All tests use the raw client.
Garbage/binary payloads are fixed deterministic sequences — not random.
"""

from __future__ import annotations

import asyncio
import json
import time

from mcp_test_harness.client.protocol import get_error_code
from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult

# Fixed binary sequence 0x00–0xFF for deterministic reproducibility
_BINARY_GARBAGE: bytes = bytes(range(256))

# IDs for rapid-request test — chosen to avoid collision with other tests
_RAPID_REQUEST_IDS = list(range(1000, 1010))


class OperationalSuite(BaseSuite):
    """Test operational reliability: recovery from malformed input and rapid requests."""

    name = "operational"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results: list[TestResult] = []

        try:
            async with StdioRawClient(
                config.command, env=config.env, timeout=config.timeout
            ) as client:
                try:
                    await client.initialize()
                except Exception as exc:
                    return [self._error("operational_init", f"could not initialize: {exc}")]

                tests = [
                    self._test_partial_json,
                    self._test_empty_line,
                    self._test_binary_garbage,
                    self._test_rapid_sequential_requests,
                    self._test_large_request,
                    self._test_response_time_baseline,
                    self._test_concurrent_notifications,
                    self._test_unknown_notification,
                ]

                server_alive = True
                for test_fn in tests:
                    if not server_alive:
                        results.append(
                            self._skip(
                                test_fn.__name__.lstrip("_test_"),
                                "skipped — server unresponsive",
                            )
                        )
                        continue

                    t0 = time.monotonic()
                    try:
                        result = await asyncio.wait_for(
                            test_fn(client, config),
                            timeout=config.timeout * 2,
                        )
                    except asyncio.TimeoutError:
                        result = self._fail(
                            test_fn.__name__.lstrip("_test_"),
                            "test timed out",
                        )
                        server_alive = False
                    except asyncio.CancelledError as exc:
                        result = self._error(
                            test_fn.__name__.lstrip("_test_"),
                            f"test cancelled: {exc}",
                        )
                        server_alive = False
                    except Exception as exc:
                        result = self._error(
                            test_fn.__name__.lstrip("_test_"),
                            f"unexpected harness error: {exc}",
                        )

                    result.duration_ms = (time.monotonic() - t0) * 1000
                    results.append(result)

                    # Stop running tests if the server is confirmed unresponsive
                    if result.status == "fail" and "unresponsive" in result.detail:
                        server_alive = False

        except asyncio.CancelledError as exc:
            results.append(self._error("operational", f"suite cancelled: {exc}"))
        except Exception as exc:
            results.append(self._error("operational", f"suite-level error: {exc}"))

        return results

    # ------------------------------------------------------------------ #
    # Recovery helper
    # ------------------------------------------------------------------ #

    async def _ping_recovery(
        self, client: StdioRawClient, config: ServerConfig, test_name: str
    ) -> TestResult:
        """
        Verify the server is still responsive by sending a ping.
        Returns a pass result on success, fail with 'unresponsive' on timeout.
        """
        try:
            response = await client.send_request("ping", id=9999)
            if response.get("result") == {}:
                return self._pass(test_name, "server recovered")
            return self._warn(test_name, f"unexpected ping response after adversarial input: {response}")
        except ReadTimeout:
            return self._fail(test_name, "server unresponsive after adversarial input")

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #

    async def _test_partial_json(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "partial_json"
        # Send incomplete JSON then a valid ping
        await client.send_raw(b'{"jsonrpc": "2.0", "id": 1, "met\n')
        # Drain any error response (may or may not arrive)
        for _ in range(2):
            try:
                msg = await client.read_message(timeout=min(2.0, config.timeout))
                if "method" in msg and "id" not in msg:
                    continue  # skip notifications
                break
            except ReadTimeout:
                break
        return await self._ping_recovery(client, config, name)

    async def _test_empty_line(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "empty_line"
        await client.send_raw(b"\n")
        return await self._ping_recovery(client, config, name)

    async def _test_binary_garbage(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "binary_garbage"
        await client.send_raw(_BINARY_GARBAGE + b"\n")
        # Give the server a moment to process (it may return a parse error)
        for _ in range(2):
            try:
                msg = await client.read_message(timeout=min(2.0, config.timeout))
                if "method" in msg and "id" not in msg:
                    continue
                break
            except ReadTimeout:
                break
        return await self._ping_recovery(client, config, name)

    async def _test_rapid_sequential_requests(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "rapid_sequential_requests"
        n = len(_RAPID_REQUEST_IDS)

        # Fire all pings without waiting for responses, then collect.
        # Use send_raw so we can fire all before reading any response.
        for req_id in _RAPID_REQUEST_IDS:
            msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "ping"}) + "\n"
            await client.send_raw(msg.encode())

        # Collect responses
        received: set[int] = set()
        deadline = time.monotonic() + config.timeout
        while len(received) < n and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                resp = await client.read_message(timeout=max(0.1, remaining))
            except ReadTimeout:
                break
            resp_id = resp.get("id")
            if isinstance(resp_id, int) and resp_id in _RAPID_REQUEST_IDS:
                received.add(resp_id)

        if len(received) == n:
            return self._pass(name, f"all {n} rapid ping responses received with correct IDs")
        dropped = n - len(received)
        if dropped < n:
            return self._warn(name, f"server dropped {dropped} of {n} rapid requests")
        return self._fail(name, "server unresponsive — all rapid requests dropped")

    async def _test_large_request(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "large_request"
        # Find a string-argument tool
        try:
            tlist = await client.send_request("tools/list")
        except ReadTimeout:
            return self._skip(name, "tools/list timed out — cannot find string-arg tool")

        if "error" in tlist:
            return self._skip(name, "tools/list returned error")

        tools = tlist.get("result", {}).get("tools", [])
        target = None
        for tool in tools:
            schema = tool.get("inputSchema") or {}
            props = schema.get("properties") or {}
            for arg_name, arg_schema in props.items():
                if isinstance(arg_schema, dict) and arg_schema.get("type") == "string":
                    target = {"name": tool["name"], "arg": arg_name, "schema": schema}
                    break
            if target:
                break

        if target is None:
            return self._skip(name, "no string-argument tool available")

        # Build minimal required args then inject 1MB string
        schema = target["schema"]
        required: set[str] = set(schema.get("required") or [])
        props = schema.get("properties") or {}
        args: dict = {}
        for field_name in required:
            prop = props.get(field_name) or {}
            if field_name == target["arg"]:
                args[field_name] = "A" * (1024 * 1024)
            else:
                typ = prop.get("type", "string") if isinstance(prop, dict) else "string"
                args[field_name] = _default_for_type(typ)
        if target["arg"] not in args:
            args[target["arg"]] = "A" * (1024 * 1024)

        try:
            response = await client.send_request(
                "tools/call",
                params={"name": target["name"], "arguments": args},
            )
        except ReadTimeout:
            return self._fail(name, f"server timed out or crashed on 1MB input to {target['name']!r}")

        if "error" in response or response.get("result", {}).get("isError"):
            return self._pass(name, f"server rejected 1MB argument for {target['name']!r}")
        return self._pass(name, f"server processed 1MB argument without error (no size limit) for {target['name']!r}")

    async def _test_response_time_baseline(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "response_time_baseline"
        measurements: list[str] = []

        t0 = time.monotonic()
        try:
            await client.send_request("ping", id=2001)
            ping_ms = (time.monotonic() - t0) * 1000
            measurements.append(f"ping: {ping_ms:.0f}ms")
        except ReadTimeout:
            measurements.append("ping: timeout")

        t0 = time.monotonic()
        try:
            await client.send_request("tools/list", id=2002)
            list_ms = (time.monotonic() - t0) * 1000
            measurements.append(f"tools/list: {list_ms:.0f}ms")
        except ReadTimeout:
            measurements.append("tools/list: timeout")

        return self._pass(name, ", ".join(measurements))

    async def _test_concurrent_notifications(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "concurrent_notifications"
        # Send 5 notifications/initialized in rapid succession
        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        for _ in range(5):
            await client.send_raw(notif.encode())
        return await self._ping_recovery(client, config, name)

    async def _test_unknown_notification(
        self, client: StdioRawClient, config: ServerConfig
    ) -> TestResult:
        name = "unknown_notification"
        notif = json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/harness_test_unknown_xyz"}
        ) + "\n"
        await client.send_raw(notif.encode())
        return await self._ping_recovery(client, config, name)


# ------------------------------------------------------------------ #
# Pure helpers
# ------------------------------------------------------------------ #

def _default_for_type(typ: str) -> object:
    if typ in ("number", "integer"):
        return 0
    if typ == "boolean":
        return False
    if typ == "array":
        return []
    if typ == "object":
        return {}
    return "test"
