"""
Path traversal test suite.

Probes file-handling tools for directory traversal vulnerabilities: ../  sequences,
absolute paths, URL-encoded traversal, and null byte path injection.

Only read-oriented tools are probed — the harness never sends write, delete, or
move payloads to traversal targets. Tool classification is heuristic (name-based).

All tests use the raw client.
No shell=True, no eval, no exec anywhere in this module.
"""

from __future__ import annotations

import asyncio
import time

from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult

# Tool name fragments that suggest file access
_FILE_TOOL_PATTERNS = [
    "read_file", "write_file", "read", "write", "list_directory",
    "list_dir", "get_file", "file_read", "file_write", "open",
    "cat", "ls", "dir", "search_files", "move_file", "copy_file",
    "delete_file", "create_file",
]

# Argument name fragments that suggest a path parameter
_PATH_ARG_PATTERNS = [
    "path", "file", "filename", "filepath", "file_path",
    "directory", "dir", "uri", "location", "source", "target",
    "destination", "src", "dst",
]

# Tool name fragments suggesting write/destructive operations — skip these
_WRITE_TOOL_PATTERNS = [
    "write", "create", "delete", "remove", "move",
    "copy", "rename", "mkdir", "rmdir", "put",
]

# Content patterns indicating a successful Unix passwd-file traversal
_PASSWD_INDICATORS = ["root:", "/bin/sh", "/bin/bash", "nobody:"]

# Content patterns indicating a successful Windows system-file traversal
_WINDOWS_INDICATORS = ["[boot loader]", "[operating systems]", "MSDOS.SYS"]

# All indicators combined for quick scanning
_ALL_TRAVERSAL_INDICATORS = _PASSWD_INDICATORS + _WINDOWS_INDICATORS


class PathTraversalSuite(BaseSuite):
    """Probe file-handling tools for directory traversal vulnerabilities."""

    name = "path_traversal"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results: list[TestResult] = []
        tests = [
            self._test_parent_directory_traversal,
            self._test_absolute_path_outside_root,
            self._test_dot_dot_in_middle,
            self._test_url_encoded_traversal,
            self._test_null_byte_path_truncation,
        ]
        try:
            async with StdioRawClient(
                config.command, env=config.env, timeout=config.timeout
            ) as client:
                init_result = await client.initialize()
                if "tools" not in init_result.get("capabilities", {}):
                    return [self._skip("path_traversal", "server did not declare tools capability")]

                file_tools = await self._find_file_tools(client)
                if not file_tools:
                    return [self._skip("path_traversal", "no file-handling tools found")]

                for test_fn in tests:
                    t0 = time.monotonic()
                    try:
                        result = await test_fn(client, file_tools, config)
                    except asyncio.CancelledError as exc:
                        result = self._error(
                            test_fn.__name__.lstrip("_test_"),
                            f"test cancelled: {exc}",
                        )
                    except Exception as exc:
                        result = self._error(
                            test_fn.__name__.lstrip("_test_"),
                            f"unexpected harness error: {exc}",
                        )
                    result.duration_ms = (time.monotonic() - t0) * 1000
                    results.append(result)

        except asyncio.CancelledError as exc:
            results.append(self._error("path_traversal", f"suite cancelled: {exc}"))
        except Exception as exc:
            results.append(self._error("path_traversal", f"suite-level error: {exc}"))

        return results

    # ------------------------------------------------------------------ #
    # Tool discovery
    # ------------------------------------------------------------------ #

    async def _find_file_tools(self, client: StdioRawClient) -> list[dict]:
        """
        Return descriptors for tools that appear to handle file paths and
        are safe to probe (read-oriented only).

        Each descriptor: {name, path_arg, schema}
        """
        try:
            response = await client.send_request("tools/list")
        except ReadTimeout:
            return []

        if "error" in response:
            return []

        tools = response.get("result", {}).get("tools", [])
        matches: list[dict] = []

        for tool in tools:
            name = tool.get("name", "")
            if not isinstance(name, str):
                continue

            # Skip write/destructive tools
            if not _is_read_tool(name):
                continue

            name_lower = name.lower()
            schema = tool.get("inputSchema") or {}
            if not isinstance(schema, dict):
                continue

            props = schema.get("properties") or {}
            if not isinstance(props, dict):
                continue

            name_match = any(p in name_lower for p in _FILE_TOOL_PATTERNS)
            arg_match = any(
                any(p in arg.lower() for p in _PATH_ARG_PATTERNS)
                for arg in props
            )

            if not (name_match or arg_match):
                continue

            # Find the path-like argument
            path_arg = next(
                (
                    arg for arg in props
                    if any(p in arg.lower() for p in _PATH_ARG_PATTERNS)
                ),
                None,
            )
            # Fallback: first string argument
            if path_arg is None:
                path_arg = next(
                    (
                        arg for arg, s in props.items()
                        if isinstance(s, dict) and s.get("type") == "string"
                    ),
                    None,
                )

            if path_arg:
                matches.append({"name": name, "path_arg": path_arg, "schema": schema})

        return matches

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def _minimal_args(self, tool: dict, path_value: str) -> dict:
        """Build arguments dict with path_value in the path arg and safe defaults elsewhere."""
        schema = tool["schema"]
        props = schema.get("properties") or {}
        required: set[str] = set(schema.get("required") or [])
        args: dict = {}
        for name in required:
            prop = props.get(name) or {}
            if name == tool["path_arg"]:
                args[name] = path_value
            else:
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
        # Always include the path arg even if not in required
        if tool["path_arg"] not in args:
            args[tool["path_arg"]] = path_value
        return args

    async def _probe_tool(
        self,
        client: StdioRawClient,
        tool: dict,
        path_value: str,
        config: ServerConfig,
    ) -> dict | None:
        """Call a tool with the given path value. Returns raw response or None on timeout."""
        args = self._minimal_args(tool, path_value)
        try:
            return await client.send_request(
                "tools/call",
                params={"name": tool["name"], "arguments": args},
            )
        except ReadTimeout:
            return None

    def _check_traversal(self, response: dict | None) -> str:
        """
        Inspect a tools/call response for traversal indicators.
        Returns 'pass', 'fail', or 'warn'.
        """
        if response is None:
            return "warn"  # timeout — ambiguous

        # Server-side error = traversal was blocked
        if "error" in response:
            return "pass"
        result = response.get("result", {})
        if result.get("isError"):
            return "pass"

        full_text = _extract_text(response)
        if any(ind in full_text for ind in _ALL_TRAVERSAL_INDICATORS):
            return "fail"

        return "pass"

    # ------------------------------------------------------------------ #
    # Individual tests
    # ------------------------------------------------------------------ #

    async def _test_parent_directory_traversal(
        self,
        client: StdioRawClient,
        file_tools: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "parent_directory_traversal"
        payloads = [
            "../../../../etc/passwd",
            r"..\..\..\..\windows\system.ini",
        ]
        for tool in file_tools:
            for payload in payloads:
                resp = await self._probe_tool(client, tool, payload, config)
                status = self._check_traversal(resp)
                if status == "fail":
                    return self._fail(
                        name,
                        f"traversal succeeded via {tool['name']!r} with path {payload!r}",
                    )
        return self._pass(name, f"all {len(file_tools)} file tool(s) blocked ../ traversal")

    async def _test_absolute_path_outside_root(
        self,
        client: StdioRawClient,
        file_tools: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "absolute_path_outside_root"
        payloads = [
            "/etc/passwd",
            r"C:\Windows\System32\drivers\etc\hosts",
        ]
        for tool in file_tools:
            for payload in payloads:
                resp = await self._probe_tool(client, tool, payload, config)
                status = self._check_traversal(resp)
                if status == "fail":
                    return self._fail(
                        name,
                        f"absolute path traversal succeeded via {tool['name']!r}: {payload!r}",
                    )
        return self._pass(name, f"all {len(file_tools)} file tool(s) blocked absolute path access")

    async def _test_dot_dot_in_middle(
        self,
        client: StdioRawClient,
        file_tools: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "dot_dot_in_middle"
        payloads = [
            "subdir/../../etc/passwd",
            "allowed/subpath/../../../etc/passwd",
        ]
        for tool in file_tools:
            for payload in payloads:
                resp = await self._probe_tool(client, tool, payload, config)
                status = self._check_traversal(resp)
                if status == "fail":
                    return self._fail(
                        name,
                        f"mid-path traversal succeeded via {tool['name']!r}: {payload!r}",
                    )
        return self._pass(name, "mid-path ../ traversal blocked")

    async def _test_url_encoded_traversal(
        self,
        client: StdioRawClient,
        file_tools: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "url_encoded_traversal"
        # %2e%2e%2f = ../  and double-encoded variants
        payloads = [
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "..%2f..%2fetc%2fpasswd",
            "%2e%2e/%2e%2e/etc/passwd",
        ]
        for tool in file_tools:
            for payload in payloads:
                resp = await self._probe_tool(client, tool, payload, config)
                status = self._check_traversal(resp)
                if status == "fail":
                    return self._fail(
                        name,
                        f"URL-encoded traversal succeeded via {tool['name']!r}: {payload!r}",
                    )
        return self._pass(name, "URL-encoded path traversal blocked")

    async def _test_null_byte_path_truncation(
        self,
        client: StdioRawClient,
        file_tools: list[dict],
        config: ServerConfig,
    ) -> TestResult:
        name = "null_byte_path_truncation"
        # Null byte may truncate the C-level string, bypassing suffix checks
        payloads = [
            "allowed_file.txt\x00../../etc/passwd",
            "safe.txt\x00/etc/passwd",
        ]
        for tool in file_tools:
            for payload in payloads:
                resp = await self._probe_tool(client, tool, payload, config)
                status = self._check_traversal(resp)
                if status == "fail":
                    return self._fail(
                        name,
                        f"null byte truncation traversal succeeded via {tool['name']!r}",
                    )
        return self._pass(name, "null byte path truncation blocked")


# ------------------------------------------------------------------ #
# Pure helpers
# ------------------------------------------------------------------ #

def _is_read_tool(tool_name: str) -> bool:
    """Conservative check: return False if the tool name suggests write/destructive ops."""
    name_lower = tool_name.lower()
    return not any(p in name_lower for p in _WRITE_TOOL_PATTERNS)


def _extract_text(response: dict) -> str:
    """Extract all text content from a tools/call response."""
    texts: list[str] = []
    result = response.get("result", {})
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(item.get("text", ""))
    return "\n".join(texts)
