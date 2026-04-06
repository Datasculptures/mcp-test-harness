"""
Input validation and error info-disclosure test suite.

Tests whether servers enforce their declared inputSchema types and required
fields, handle edge-case inputs gracefully, and avoid leaking sensitive
information in error responses.

All tests use the raw client.
"""

from __future__ import annotations

import asyncio
import re
import time

from mcp_test_harness.client.protocol import get_error_code
from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult

# Patterns that suggest information disclosure in error responses.
# Tuples of (regex_pattern, description).
INFO_DISCLOSURE_PATTERNS: list[tuple[str, str]] = [
    (r"/home/\w+",                            "unix home path"),
    (r"/Users/\w+",                           "macos home path"),
    (r"[A-Z]:\\\\",                           "windows path"),
    (r"/usr/",                                "system path"),
    (r"/var/",                                "system path"),
    (r"Traceback \(most recent",              "python stack trace"),
    (r'File ".*", line \d+',                  "python file reference"),
    (r"at .+:\d+:\d+",                        "javascript stack trace"),
    (r"\.py:\d+",                             "python file with line number"),
    (r"node_modules/",                        "node_modules path"),
    (r"ANTHROPIC_API_KEY|API_KEY|SECRET|PASSWORD|TOKEN",
                                              "potential credential reference"),
]

# Wrong-type substitution map: schema type → wrong value to send
_WRONG_TYPE_VALUES: dict[str, object] = {
    "string":  12345,
    "number":  "not_a_number",
    "integer": "not_a_number",
    "boolean": "not_a_bool",
    "array":   "not_an_array",
    "object":  "not_an_object",
}


class ValidationSuite(BaseSuite):
    name = "validation"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results: list[TestResult] = []
        tests = [
            self._test_wrong_type_argument,
            self._test_missing_required_argument,
            self._test_extra_unknown_argument,
            self._test_empty_string_argument,
            self._test_null_byte_argument,
            self._test_oversized_argument,
            self._test_error_info_disclosure,
            self._test_deeply_nested_object,
            self._test_array_boundary,
        ]
        for test_fn in tests:
            t0 = time.monotonic()
            try:
                result = await test_fn(config)
            except asyncio.CancelledError as exc:
                result = self._error(
                    test_fn.__name__.lstrip("_test_"),
                    f"Test cancelled: {exc}",
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
    # Helpers
    # ------------------------------------------------------------------ #

    async def _get_tools(self, config: ServerConfig) -> tuple[list[dict], str]:
        """
        Return (tools, error_msg). error_msg is '' on success.
        Uses a fresh raw client connection.
        """
        try:
            async with StdioRawClient(
                config.command, env=config.env, timeout=config.timeout
            ) as client:
                init_result = await client.initialize()
                if "tools" not in init_result.get("capabilities", {}):
                    return [], "no_tools_capability"
                try:
                    response = await client.send_request("tools/list")
                except ReadTimeout:
                    return [], "tools/list timed out"
                if "error" in response:
                    return [], f"tools/list error {get_error_code(response)}"
                tools = response.get("result", {}).get("tools", [])
                return (tools if isinstance(tools, list) else []), ""
        except Exception as exc:
            return [], str(exc)

    async def _call_tool(
        self,
        config: ServerConfig,
        tool_name: str,
        arguments: dict,
    ) -> dict | None:
        """
        Open a fresh connection, initialize, call a tool, return the raw response.
        Returns None on ReadTimeout.
        """
        async with StdioRawClient(
            config.command, env=config.env, timeout=config.timeout
        ) as client:
            await client.initialize()
            try:
                return await client.send_request(
                    "tools/call",
                    params={"name": tool_name, "arguments": arguments},
                )
            except ReadTimeout:
                return None

    def _minimal_required_args(self, tool: dict) -> dict:
        """Build a minimal set of valid arguments that satisfies all required fields."""
        schema = tool.get("inputSchema", {})
        if not isinstance(schema, dict):
            return {}
        required: set[str] = set(schema.get("required", []))
        props = schema.get("properties", {})
        args: dict = {}
        for name in required:
            prop = props.get(name, {})
            typ = prop.get("type", "string") if isinstance(prop, dict) else "string"
            if typ == "string":
                args[name] = "test"
            elif typ in ("number", "integer"):
                args[name] = 0
            elif typ == "boolean":
                args[name] = False
            elif typ == "array":
                args[name] = []
            elif typ == "object":
                args[name] = {}
            else:
                args[name] = "test"
        return args

    def _is_error_response(self, response: dict) -> bool:
        """True if response indicates an error (JSON-RPC error or isError:true)."""
        if "error" in response:
            return True
        return bool(response.get("result", {}).get("isError"))

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #

    async def _test_wrong_type_argument(self, config: ServerConfig) -> TestResult:
        """
        For each tool with a typed argument, send a value of the wrong type.
        Accept isError:true or JSON-RPC error → pass (validated).
        Normal result without error → warn.
        """
        name = "wrong_type_argument"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        # Find tools with at least one typed property
        candidates: list[tuple[dict, str, str]] = []  # (tool, arg_name, arg_type)
        for tool in tools:
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            if not isinstance(props, dict):
                continue
            for arg_name, arg_schema in props.items():
                if not isinstance(arg_schema, dict):
                    continue
                arg_type = arg_schema.get("type")
                if arg_type in _WRONG_TYPE_VALUES:
                    candidates.append((tool, arg_name, arg_type))
                    break  # one per tool is enough

        if not candidates:
            return self._skip(name, "No tools with typed arguments found")

        not_validated: list[str] = []
        for tool, arg_name, arg_type in candidates:
            wrong_value = _WRONG_TYPE_VALUES[arg_type]
            # Build args: all required fields filled correctly, except the target arg
            args = self._minimal_required_args(tool)
            args[arg_name] = wrong_value

            response = await self._call_tool(config, tool["name"], args)
            if response is None:
                continue  # timeout — skip this tool
            if not self._is_error_response(response):
                not_validated.append(
                    f"{tool['name']}.{arg_name} (sent {type(wrong_value).__name__} for {arg_type!r})"
                )

        if not_validated:
            return self._warn(
                name,
                "Input not validated against declared schema type for: "
                + ", ".join(not_validated),
            )
        return self._pass(name, f"All {len(candidates)} typed argument(s) validated correctly")

    async def _test_missing_required_argument(self, config: ServerConfig) -> TestResult:
        """
        For each tool with required arguments, call with empty arguments dict.
        Accept isError:true or error → pass. Normal result → warn.
        """
        name = "missing_required_argument"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        candidates = [
            t for t in tools
            if isinstance(t.get("inputSchema"), dict)
            and t["inputSchema"].get("required")
        ]
        if not candidates:
            return self._skip(name, "No tools with required arguments found")

        not_enforced: list[str] = []
        for tool in candidates:
            response = await self._call_tool(config, tool["name"], {})
            if response is None:
                continue
            if not self._is_error_response(response):
                required = tool["inputSchema"].get("required", [])
                not_enforced.append(f"{tool['name']} (required: {required})")

        if not_enforced:
            return self._warn(
                name,
                "Required argument not enforced for: " + ", ".join(not_enforced),
            )
        return self._pass(name, f"All {len(candidates)} tool(s) enforce required arguments")

    async def _test_extra_unknown_argument(self, config: ServerConfig) -> TestResult:
        """
        Call tool with correct required args plus an unknown extra argument.
        Both accepting and rejecting extra args are acceptable — just log behaviour.
        """
        name = "extra_unknown_argument"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        # Find any tool with a valid inputSchema
        target = next(
            (t for t in tools if isinstance(t.get("inputSchema"), dict)),
            None,
        )
        if target is None:
            return self._skip(name, "No tools with valid inputSchema found")

        args = self._minimal_required_args(target)
        args["__harness_extra_arg__"] = "test"

        response = await self._call_tool(config, target["name"], args)
        if response is None:
            return self._warn(name, "Server timed out on extra-argument call")

        if self._is_error_response(response):
            return self._pass(
                name,
                f"Server rejected extra argument (strict validation) in {target['name']!r}",
            )
        return self._pass(
            name,
            f"Server ignored extra argument (permissive behaviour) in {target['name']!r}",
        )

    async def _test_empty_string_argument(self, config: ServerConfig) -> TestResult:
        """
        For tools with required string arguments, send empty string.
        Observational only — both accept and reject are valid.
        """
        name = "empty_string_argument"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        # Find a tool with a required string argument
        target_tool = None
        target_arg = None
        for tool in tools:
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            required: set[str] = set(schema.get("required", []))
            props = schema.get("properties", {})
            for arg_name in required:
                prop = props.get(arg_name, {})
                if isinstance(prop, dict) and prop.get("type") == "string":
                    target_tool = tool
                    target_arg = arg_name
                    break
            if target_tool:
                break

        if target_tool is None:
            return self._skip(name, "No tools with required string arguments found")

        args = self._minimal_required_args(target_tool)
        args[target_arg] = ""

        response = await self._call_tool(config, target_tool["name"], args)
        if response is None:
            return self._warn(name, "Server timed out on empty string argument")

        if self._is_error_response(response):
            return self._pass(
                name,
                f"{target_tool['name']!r}.{target_arg!r}: rejected empty string",
            )
        return self._pass(
            name,
            f"{target_tool['name']!r}.{target_arg!r}: accepted empty string (permissive)",
        )

    async def _test_null_byte_argument(self, config: ServerConfig) -> TestResult:
        """
        For tools with string arguments, send a string containing a null byte.
        Error or isError:true → pass. Normal result → warn (potential truncation vector).
        """
        name = "null_byte_argument"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        # Find a tool with any string argument
        target_tool = None
        target_arg = None
        for tool in tools:
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            if not isinstance(props, dict):
                continue
            for arg_name, arg_schema in props.items():
                if isinstance(arg_schema, dict) and arg_schema.get("type") == "string":
                    target_tool = tool
                    target_arg = arg_name
                    break
            if target_tool:
                break

        if target_tool is None:
            return self._skip(name, "No tools with string arguments found")

        args = self._minimal_required_args(target_tool)
        args[target_arg] = "hello\x00world"

        response = await self._call_tool(config, target_tool["name"], args)
        if response is None:
            return self._warn(name, "Server timed out or crashed on null byte input")

        if self._is_error_response(response):
            return self._pass(
                name,
                f"{target_tool['name']!r}: rejected null byte in string argument",
            )
        return self._warn(
            name,
            f"{target_tool['name']!r}: accepted null byte in string argument "
            "(potential truncation or injection vector)",
        )

    async def _test_oversized_argument(self, config: ServerConfig) -> TestResult:
        """
        For tools with string arguments, send a 100,000 character string.
        Error or isError:true → pass. Normal result → warn. Timeout/crash → fail.
        """
        name = "oversized_argument"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        target_tool = None
        target_arg = None
        for tool in tools:
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            if not isinstance(props, dict):
                continue
            for arg_name, arg_schema in props.items():
                if isinstance(arg_schema, dict) and arg_schema.get("type") == "string":
                    target_tool = tool
                    target_arg = arg_name
                    break
            if target_tool:
                break

        if target_tool is None:
            return self._skip(name, "No tools with string arguments found")

        args = self._minimal_required_args(target_tool)
        args[target_arg] = "A" * 100_000

        response = await self._call_tool(config, target_tool["name"], args)
        if response is None:
            return self._warn(
                name,
                f"{target_tool['name']!r}: server did not respond to 100K char input "
                "(timed out — may be slow or may have crashed)",
            )

        if self._is_error_response(response):
            return self._pass(
                name,
                f"{target_tool['name']!r}: rejected oversized argument (100K chars)",
            )
        return self._warn(
            name,
            f"{target_tool['name']!r}: accepted 100K character argument without error "
            "(no input size limit detected)",
        )

    async def _test_error_info_disclosure(self, config: ServerConfig) -> TestResult:
        """
        Trigger errors via (1) nonexistent tool call and (2) missing required args,
        then scan error text for sensitive information patterns (paths, stack traces,
        credential references).

        Only checks error responses — tools may legitimately mention paths in success output.
        """
        name = "error_info_disclosure"

        # Collect error response text via a single connection
        error_texts: list[str] = []

        try:
            async with StdioRawClient(
                config.command, env=config.env, timeout=config.timeout
            ) as client:
                init_result = await client.initialize()
                has_tools = "tools" in init_result.get("capabilities", {})

                # Method 1: call a nonexistent tool
                try:
                    r = await client.send_request(
                        "tools/call",
                        params={
                            "name": "__harness_nonexistent_tool__",
                            "arguments": {},
                        },
                    )
                    error_texts.extend(_extract_error_text(r))
                except ReadTimeout:
                    pass

                # Method 2: call real tool with missing required args (if tools available)
                if has_tools:
                    try:
                        tlist = await client.send_request("tools/list")
                    except ReadTimeout:
                        tlist = {}

                    tools_raw = tlist.get("result", {}).get("tools", [])
                    for tool in tools_raw:
                        schema = tool.get("inputSchema")
                        if isinstance(schema, dict) and schema.get("required"):
                            try:
                                r = await client.send_request(
                                    "tools/call",
                                    params={"name": tool["name"], "arguments": {}},
                                )
                                error_texts.extend(_extract_error_text(r))
                            except ReadTimeout:
                                pass
                            break  # one is enough

        except asyncio.CancelledError:
            return self._error(name, "Test cancelled")
        except Exception as exc:
            return self._error(name, f"Unexpected error: {exc}")

        if not error_texts:
            return self._skip(
                name, "No error responses received — cannot check for info disclosure"
            )

        combined = "\n".join(error_texts)
        found: list[str] = []
        for pattern, description in INFO_DISCLOSURE_PATTERNS:
            if re.search(pattern, combined):
                found.append(description)

        if found:
            return self._warn(
                name,
                "Sensitive information in error response: " + ", ".join(found),
            )
        return self._pass(name, f"No info disclosure patterns found in {len(error_texts)} error response(s)")

    async def _test_deeply_nested_object(self, config: ServerConfig) -> TestResult:
        """
        Send a 50-level deeply nested object as a tool argument.
        Server crash or timeout → warn. Any response (success or error) → pass.
        Tests parser depth limits and stack overflow resistance.
        """
        name = "deeply_nested_object"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        # Find any tool with at least one argument
        target_tool = None
        target_arg = None
        for tool in tools:
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            if props:
                target_tool = tool
                target_arg = next(iter(props))
                break

        if target_tool is None:
            return self._skip(name, "No tools with arguments found")

        # Build 50-level nested object: {"a": {"a": {"a": ...}}}
        nested: dict = {}
        cursor = nested
        for _ in range(49):
            cursor["a"] = {}
            cursor = cursor["a"]
        cursor["a"] = "leaf"

        args = self._minimal_required_args(target_tool)
        args[target_arg] = nested

        response = await self._call_tool(config, target_tool["name"], args)
        if response is None:
            return self._warn(
                name,
                f"{target_tool['name']!r}: server timed out on 50-level nested object "
                "(may have crashed or hung)",
            )
        return self._pass(
            name,
            f"{target_tool['name']!r}: handled 50-level nested object (responded without hanging)",
        )

    async def _test_array_boundary(self, config: ServerConfig) -> TestResult:
        """
        Send a 10,000-element array as a tool argument.
        Server crash or timeout → warn. Any response (success or error) → pass.
        Tests array size limits and memory handling.
        """
        name = "array_boundary"
        tools, err = await self._get_tools(config)
        if err == "no_tools_capability":
            return self._skip(name, "Server did not declare tools capability")
        if err:
            return self._error(name, err)

        # Find any tool with at least one argument
        target_tool = None
        target_arg = None
        for tool in tools:
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            if props:
                target_tool = tool
                target_arg = next(iter(props))
                break

        if target_tool is None:
            return self._skip(name, "No tools with arguments found")

        args = self._minimal_required_args(target_tool)
        args[target_arg] = ["x"] * 10_000

        response = await self._call_tool(config, target_tool["name"], args)
        if response is None:
            return self._warn(
                name,
                f"{target_tool['name']!r}: server timed out on 10,000-element array "
                "(may have crashed or hung)",
            )
        return self._pass(
            name,
            f"{target_tool['name']!r}: handled 10,000-element array (responded without hanging)",
        )


# ------------------------------------------------------------------ #
# Pure helpers
# ------------------------------------------------------------------ #

def _extract_error_text(response: dict) -> list[str]:
    """
    Extract text that should be checked for info disclosure from an error response.
    Returns empty list if the response is a success (not an error).
    """
    texts: list[str] = []

    # JSON-RPC error field
    if "error" in response:
        err = response["error"]
        if isinstance(err, dict):
            if isinstance(err.get("message"), str):
                texts.append(err["message"])
            data = err.get("data")
            if isinstance(data, str):
                texts.append(data)
            elif isinstance(data, dict):
                texts.append(str(data))
        return texts

    # isError:true in result — check content
    result = response.get("result", {})
    if result.get("isError"):
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))

    return texts
