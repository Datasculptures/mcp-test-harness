"""
Tool conformance test suite.

Inspection tests (listing, field checks, schema validation) use the raw client
so they can detect non-conformant server output that the SDK might sanitize.

Happy-path invocation tests use the SDK client (known-correct client).
Adversarial invocation tests (unknown tool, missing args) use the raw client.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from jsonschema import Draft202012Validator

from mcp_test_harness.client.protocol import get_error_code
from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.client.stdio_sdk import StdioSdkClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult

# MCP tool name pattern: 1-128 chars, alphanumeric plus _ - .
_TOOL_NAME_RE = re.compile(r'^[A-Za-z0-9_\-.]{1,128}$')


def _minimal_args(schema: dict) -> dict:
    """
    Build a minimal set of valid arguments from a JSON Schema.
    Provides a type-appropriate default for each required field.
    """
    if not isinstance(schema, dict):
        return {}
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    args: dict = {}
    for key in required:
        prop = properties.get(key, {})
        typ = prop.get("type", "string")
        if typ == "string":
            args[key] = "test"
        elif typ in ("number", "integer"):
            args[key] = 0
        elif typ == "boolean":
            args[key] = False
        elif typ == "array":
            args[key] = []
        elif typ == "object":
            args[key] = {}
        else:
            args[key] = "test"
    return args


async def _raw_list_tools(client: StdioRawClient, timeout: float) -> tuple[list[dict] | None, str]:
    """
    Call tools/list via raw client. Returns (tools, error_msg).
    error_msg is '' on success, "no_tools_capability" if not declared,
    or an error string on failure.
    """
    try:
        response = await client.send_request("tools/list")
    except ReadTimeout:
        return None, "tools/list timed out"

    if "error" in response:
        code = get_error_code(response)
        if code == -32601:
            return None, "no_tools_capability"
        return None, f"tools/list returned error {code}"

    tools = response.get("result", {}).get("tools")
    if not isinstance(tools, list):
        return None, f"result.tools is not an array: {type(tools)}"
    return tools, ""


async def _get_tools_raw(config: ServerConfig) -> tuple[list[dict] | None, str]:
    """Initialize and list tools using raw client."""
    try:
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            # Check tools capability from initialize response
            init_result = await client.initialize()
            caps = init_result.get("capabilities", {})
            if "tools" not in caps:
                return None, "no_tools_capability"
            return await _raw_list_tools(client, config.timeout)
    except Exception as exc:
        return None, str(exc)


class ToolsSuite(BaseSuite):
    name = "tools"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results = []
        tests = [
            self._test_tools_list_returns_array,
            self._test_tool_has_required_fields,
            self._test_tool_name_valid,
            self._test_input_schema_valid_jsonschema,
            self._test_output_schema_valid_if_present,
            self._test_tool_call_valid,
            self._test_tool_call_unknown_name,
            self._test_tool_call_missing_required_args,
            self._test_pagination_if_available,
        ]
        for test_fn in tests:
            t0 = time.monotonic()
            try:
                result = await test_fn(config)
            except asyncio.CancelledError as exc:
                # anyio cancel scope leak from a misbehaving server — record as error, don't re-raise
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

    async def _test_tools_list_returns_array(self, config: ServerConfig) -> TestResult:
        name = "tools_list_returns_array"
        tools, err = await _get_tools_raw(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)
        return self._pass(name, f"{len(tools)} tool(s) listed")  # type: ignore[arg-type]

    async def _test_tool_has_required_fields(self, config: ServerConfig) -> TestResult:
        name = "tool_has_required_fields"
        tools, err = await _get_tools_raw(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)
        if not tools:
            return self._skip(name, "tools/list returned empty array")

        violations = []
        for i, tool in enumerate(tools):
            tool_id = tool.get("name", f"tool[{i}]")
            if "name" not in tool or not isinstance(tool["name"], str):
                violations.append(f"{tool_id}: missing or non-string 'name'")
            if "inputSchema" not in tool:
                violations.append(f"{tool_id}: missing 'inputSchema'")
            elif tool["inputSchema"] is None:
                violations.append(f"{tool_id}: 'inputSchema' is null (must be a dict)")
            elif not isinstance(tool["inputSchema"], dict):
                violations.append(f"{tool_id}: 'inputSchema' is not a dict")

        if violations:
            return self._fail(name, "; ".join(violations))
        return self._pass(name, f"All {len(tools)} tool(s) have required fields")

    async def _test_tool_name_valid(self, config: ServerConfig) -> TestResult:
        name = "tool_name_valid"
        tools, err = await _get_tools_raw(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)
        if not tools:
            return self._skip(name, "tools/list returned empty array")

        violations = []
        seen_names: set[str] = set()
        for tool in tools:
            tool_name = tool.get("name", "")
            if not isinstance(tool_name, str) or not tool_name:
                violations.append(f"Empty or non-string tool name: {tool_name!r}")
                continue
            if not _TOOL_NAME_RE.match(tool_name):
                violations.append(
                    f"Invalid tool name {tool_name!r}: must match ^[A-Za-z0-9_\\-.]{{1,128}}$"
                )
            if tool_name in seen_names:
                violations.append(f"Duplicate tool name: {tool_name!r}")
            seen_names.add(tool_name)

        if violations:
            return self._fail(name, "; ".join(violations))
        return self._pass(name, f"All {len(tools)} tool name(s) are valid")

    async def _test_input_schema_valid_jsonschema(self, config: ServerConfig) -> TestResult:
        name = "input_schema_valid_jsonschema"
        tools, err = await _get_tools_raw(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)
        if not tools:
            return self._skip(name, "tools/list returned empty array")

        violations = []
        for tool in tools:
            tool_name = tool.get("name", "?")
            schema = tool.get("inputSchema")
            if schema is None:
                violations.append(f"{tool_name}: inputSchema is null")
                continue
            if not isinstance(schema, dict):
                violations.append(f"{tool_name}: inputSchema is not a dict")
                continue
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as exc:
                violations.append(f"{tool_name}: inputSchema is invalid JSON Schema: {exc}")

        if violations:
            return self._fail(name, "; ".join(violations))
        return self._pass(name, f"All {len(tools)} inputSchema(s) are valid JSON Schema")

    async def _test_output_schema_valid_if_present(self, config: ServerConfig) -> TestResult:
        name = "output_schema_valid_if_present"
        tools, err = await _get_tools_raw(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        tools_with_output_schema = [t for t in (tools or []) if t.get("outputSchema") is not None]
        if not tools_with_output_schema:
            return self._skip(name, "No tools declared outputSchema")

        violations = []
        for tool in tools_with_output_schema:
            tool_name = tool.get("name", "?")
            schema = tool["outputSchema"]
            if not isinstance(schema, dict):
                violations.append(f"{tool_name}: outputSchema is not a dict")
                continue
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as exc:
                violations.append(f"{tool_name}: outputSchema is invalid JSON Schema: {exc}")

        if violations:
            return self._fail(name, "; ".join(violations))
        return self._pass(name, f"{len(tools_with_output_schema)} outputSchema(s) are valid JSON Schema")

    async def _test_tool_call_valid(self, config: ServerConfig) -> TestResult:
        """
        Call first conformant tool with minimal valid args using the raw client.

        Note: The SDK client is ideal here (verifies conformance against a known-good
        client) but using it against non-conformant servers causes anyio cancel scope
        leaks that corrupt subsequent tests. Raw client is used for reliability.
        """
        name = "tool_call_valid"

        # List tools and call via raw client (avoids anyio cancel scope leaks on bad servers)
        tools, err = await _get_tools_raw(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)
        if not tools:
            return self._skip(name, "tools/list returned empty array")

        target = next((t for t in tools if isinstance(t.get("inputSchema"), dict)), None)
        if target is None:
            return self._skip(name, "No tools with valid inputSchema to call")

        args = _minimal_args(target["inputSchema"])
        tool_name = target["name"]

        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            try:
                response = await client.send_request(
                    "tools/call",
                    params={"name": tool_name, "arguments": args},
                )
            except ReadTimeout:
                return self._fail(name, f"tools/call {tool_name!r} timed out")

        if "error" in response:
            code = get_error_code(response)
            return self._fail(name, f"tools/call {tool_name!r} returned JSON-RPC error {code}")

        result = response.get("result", {})
        if result.get("isError"):
            return self._fail(name, f"tools/call {tool_name!r} returned isError=true")

        content = result.get("content")
        if not isinstance(content, list):
            return self._fail(name, f"tools/call result.content is not an array: {type(content)}")

        # If outputSchema declared, verify structuredContent present
        if target.get("outputSchema") is not None and "structuredContent" not in result:
            return self._warn(
                name,
                f"Tool {tool_name!r} declared outputSchema but response has no structuredContent",
            )

        return self._pass(name, f"tools/call {tool_name!r} succeeded with {len(content)} content item(s)")

    async def _test_tool_call_unknown_name(self, config: ServerConfig) -> TestResult:
        """
        tools/call with a nonexistent tool name.
        Expect either:
          - JSON-RPC error -32602, OR
          - result with isError:true
        Both are spec-conformant per 2025-11-25.
        """
        name = "tool_call_unknown_name"

        # Check tools capability
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            init_result = await client.initialize()
            caps = init_result.get("capabilities", {})
            if "tools" not in caps:
                return self._skip(name, "Server did not declare tools capability")

            try:
                response = await client.send_request(
                    "tools/call",
                    params={"name": "__nonexistent_tool_harness_test__", "arguments": {}},
                )
            except ReadTimeout:
                return self._fail(name, "Server did not respond to tools/call with unknown tool name")

        if "error" in response:
            code = get_error_code(response)
            if code == -32602:
                return self._pass(name, "Server returned -32602 for unknown tool name")
            return self._warn(
                name,
                f"Server returned error code {code} for unknown tool (expected -32602)",
            )

        result = response.get("result", {})
        if result.get("isError"):
            return self._pass(name, "Server returned isError:true for unknown tool name")

        return self._fail(name, "Server returned success for nonexistent tool name")

    async def _test_tool_call_missing_required_args(self, config: ServerConfig) -> TestResult:
        """
        Call a tool with required args omitted.
        Accept isError:true in result OR JSON-RPC error (both spec-conformant per 2025-11-25).
        """
        name = "tool_call_missing_required_args"

        # Find a tool with at least one required arg using raw client
        tools, err = await _get_tools_raw(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        target = next(
            (
                t for t in (tools or [])
                if isinstance(t.get("inputSchema"), dict)
                and t["inputSchema"].get("required")
            ),
            None,
        )
        if target is None:
            return self._skip(name, "No tools with required arguments found")

        tool_name = target["name"]

        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            try:
                response = await client.send_request(
                    "tools/call",
                    params={"name": tool_name, "arguments": {}},
                )
            except ReadTimeout:
                return self._fail(name, "Server did not respond to tools/call with missing args")

        if "error" in response:
            code = get_error_code(response)
            return self._pass(name, f"Server returned JSON-RPC error {code} for missing args")

        result = response.get("result", {})
        if result.get("isError"):
            return self._pass(name, f"Server returned isError:true for missing required args in {tool_name!r}")

        return self._warn(
            name,
            f"Server processed {tool_name!r} without required args and returned no error",
        )

    async def _test_pagination_if_available(self, config: ServerConfig) -> TestResult:
        name = "pagination_if_available"

        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            try:
                response = await client.send_request("tools/list")
            except ReadTimeout:
                return self._skip(name, "Server did not respond to tools/list")

        if "error" in response:
            return self._skip(name, f"tools/list returned error: {get_error_code(response)}")

        result = response.get("result", {})
        next_cursor = result.get("nextCursor")
        if next_cursor is None:
            return self._skip(name, "No nextCursor returned; pagination not in use")

        # Server supports pagination — test a second page request
        async with StdioRawClient(config.command, env=config.env, timeout=config.timeout) as client:
            await client.initialize()
            try:
                page2 = await client.send_request(
                    "tools/list", params={"cursor": next_cursor}
                )
            except ReadTimeout:
                return self._fail(name, "Server did not respond to paginated tools/list")

        if "error" in page2:
            return self._fail(
                name,
                f"Paginated tools/list returned error: {get_error_code(page2)}",
            )

        page2_result = page2.get("result", {})
        if "tools" not in page2_result or not isinstance(page2_result["tools"], list):
            return self._fail(name, "Paginated response missing tools array")

        return self._pass(name, f"Pagination works; page 2 has {len(page2_result['tools'])} tool(s)")
