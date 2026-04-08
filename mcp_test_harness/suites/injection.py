"""
Shell injection probe suite.

Sends shell metacharacter payloads as tool arguments and checks for
filesystem side-effects to detect genuine shell execution.

Detection signal — filesystem side-effect (Option A):
  Each payload, if passed through a shell (e.g. subprocess(..., shell=True)),
  creates a unique marker file in a harness-controlled scratch directory.
  After the tool call, the harness checks whether the file was created:
    - File exists  → shell command was executed → FAIL
    - No file      → no shell execution (tokenizer, echo, rejection) → PASS

  This approach has zero false positives against tokenizer-backed tools
  (ChromaDB, SQLite FTS5, Meilisearch, etc.) because tokenizers cannot
  create files. It also avoids false positives against tools that echo,
  store, or normalize the input string.

Env var probes check whether the server expands variables like ${HOME}
instead of treating them as opaque strings (separate detection logic).

All tests use the raw client and run within a single server process.
Payloads are hardcoded constants — never derived from external input.
No shell=True, no eval, no subprocess in this module.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult

def _make_canary_payloads(canary_dir: Path) -> list[tuple[str, str, Path]]:
    """
    Build shell metacharacter payloads that create a unique marker file if
    the argument is ever passed to a shell.

    Returns list of (payload_string, short_name, canary_file).

    Platform branching:
      Unix  — uses 'touch "<path>"'  (works in sh/bash)
      Windows — uses 'echo.>"<path>"'  (works in cmd.exe)

    On Windows, cmd.exe does not treat ';', backtick, or '$()' as separators,
    so those variants will not create files even against a genuinely vulnerable
    cmd.exe-based server. They are still included because they produce zero false
    positives against tokenizer-backed tools (no file creation either way), and
    some Windows servers may use bash or PowerShell instead of cmd.exe.
    """
    if os.name == "nt":
        def _create_cmd(p: Path) -> str:
            return f'echo.>"{p}"'
    else:
        def _create_cmd(p: Path) -> str:
            return f'touch "{p}"'

    variants = [
        ("semicolon",    "hello; {cmd}"),
        ("pipe",         "hello| {cmd}"),
        ("backtick",     "hello`{cmd}`"),
        ("dollar_paren", "hello$({cmd})"),
        ("ampersand",    "hello && {cmd}"),
    ]
    result: list[tuple[str, str, Path]] = []
    for short_name, template in variants:
        canary_file = canary_dir / f"canary_{short_name}.marker"
        payload = template.format(cmd=_create_cmd(canary_file))
        result.append((payload, short_name, canary_file))
    return result


# (payload_string, short_name)
# These payloads test whether the server expands env vars instead of
# treating them as opaque strings.
ENV_PAYLOADS: list[tuple[str, str]] = [
    ("${HOME}",        "brace_HOME"),
    ("${PATH}",        "brace_PATH"),
    ("$HOME",          "dollar_HOME"),
    ("%USERPROFILE%",  "pct_USERPROFILE"),
]

# Patterns that suggest a Unix or Windows path was substituted into output.
# A path-like expansion would contain directory separators and alphanumeric segments.
_UNIX_PATH_RE = re.compile(r"/[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+")
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\"'\n<>|]{3,}")


class InjectionSuite(BaseSuite):
    name = "injection"

    async def run(self, config: ServerConfig) -> list[TestResult]:
        results: list[TestResult] = []
        canary_dir = Path(tempfile.mkdtemp(prefix="mcp_harness_canary_"))
        try:
            async with StdioRawClient(
                config.command, env=config.env, timeout=config.timeout
            ) as client:
                await client.initialize()
                string_tools = await self._find_string_tools(client, config.timeout)

                if not string_tools:
                    results.append(
                        self._skip(
                            "injection_probing",
                            "no tools with string-typed arguments found",
                        )
                    )
                    return results

                canary_payloads = _make_canary_payloads(canary_dir)

                for payload, payload_name, canary_file in canary_payloads:
                    for tool_info in string_tools:
                        test_name = f"shell_metachar_{payload_name}__{tool_info['name']}"
                        t0 = time.monotonic()
                        result = await self._run_one(
                            client, config, test_name, tool_info, payload, canary_file
                        )
                        result.duration_ms = (time.monotonic() - t0) * 1000
                        results.append(result)

                for payload, payload_name in ENV_PAYLOADS:
                    for tool_info in string_tools:
                        test_name = f"env_var_{payload_name}__{tool_info['name']}"
                        t0 = time.monotonic()
                        result = await self._run_env_probe(
                            client, config, test_name, tool_info, payload
                        )
                        result.duration_ms = (time.monotonic() - t0) * 1000
                        results.append(result)

        except asyncio.CancelledError as exc:
            results.append(
                self._error("injection_probing", f"Suite cancelled: {exc}")
            )
        except Exception as exc:
            results.append(
                self._error("injection_probing", f"Suite-level error: {exc}")
            )
        finally:
            shutil.rmtree(canary_dir, ignore_errors=True)

        return results

    # ------------------------------------------------------------------ #
    # Tool discovery
    # ------------------------------------------------------------------ #

    async def _find_string_tools(
        self, client: StdioRawClient, timeout: float
    ) -> list[dict]:
        """
        Return a list of tool descriptors for tools that have at least one
        string-typed argument. Each descriptor has keys:
          name      — tool name
          arg_name  — the string argument to inject into
          schema    — full inputSchema dict
        """
        try:
            response = await client.send_request("tools/list")
        except ReadTimeout:
            return []

        if "error" in response:
            return []

        tools = response.get("result", {}).get("tools", [])
        string_tools: list[dict] = []
        for tool in tools:
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties", {})
            if not isinstance(props, dict):
                continue
            for arg_name, arg_schema in props.items():
                if isinstance(arg_schema, dict) and arg_schema.get("type") == "string":
                    string_tools.append(
                        {
                            "name": tool["name"],
                            "arg_name": arg_name,
                            "schema": schema,
                        }
                    )
                    break  # one string arg per tool is enough
        return string_tools

    # ------------------------------------------------------------------ #
    # Per-test execution
    # ------------------------------------------------------------------ #

    async def _run_one(
        self,
        client: StdioRawClient,
        config: ServerConfig,
        test_name: str,
        tool_info: dict,
        payload: str,
        canary_file: Path,
    ) -> TestResult:
        arguments = _build_args(tool_info, payload)
        try:
            await client.send_request(
                "tools/call",
                params={"name": tool_info["name"], "arguments": arguments},
            )
        except ReadTimeout:
            return self._warn(
                test_name,
                "server timed out on injection payload — may have crashed or hung",
            )
        except asyncio.CancelledError:
            return self._error(test_name, "test cancelled")

        # Filesystem side-effect check: if the server passed the argument to a shell,
        # the shell created the canary file. Tokenizers, echo tools, and rejection
        # paths cannot create files, so this signal has no false positives against them.
        if canary_file.exists():
            return self._fail(
                test_name,
                f"shell command executed — canary file created ({canary_file.name})",
            )
        return self._pass(test_name)

    async def _run_env_probe(
        self,
        client: StdioRawClient,
        config: ServerConfig,
        test_name: str,
        tool_info: dict,
        payload: str,
    ) -> TestResult:
        arguments = _build_args(tool_info, payload)
        try:
            response = await client.send_request(
                "tools/call",
                params={"name": tool_info["name"], "arguments": arguments},
            )
        except ReadTimeout:
            return self._warn(
                test_name,
                "server timed out on env var payload — may have crashed or hung",
            )
        except asyncio.CancelledError:
            return self._error(test_name, "test cancelled")

        status = _detect_env_expansion(payload, response)
        if status == "pass":
            return self._pass(test_name)
        if status == "warn":
            return self._warn(
                test_name,
                f"response may contain expanded value of {payload!r} — "
                "server may be interpolating environment variables",
            )
        return self._error(test_name, "unexpected detection result")


# ------------------------------------------------------------------ #
# Pure helper functions (no I/O)
# ------------------------------------------------------------------ #

def _build_args(tool_info: dict, payload_value: str) -> dict:
    """
    Build a tools/call arguments dict.
    Injects payload_value into the designated string argument.
    Fills other required arguments with safe type-appropriate defaults.
    """
    schema = tool_info["schema"]
    props = schema.get("properties", {})
    required: set[str] = set(schema.get("required", []))
    args: dict = {}
    for name, prop_schema in props.items():
        if name == tool_info["arg_name"]:
            args[name] = payload_value
        elif name in required:
            ptype = prop_schema.get("type", "string") if isinstance(prop_schema, dict) else "string"
            if ptype == "string":
                args[name] = "test"
            elif ptype in ("number", "integer"):
                args[name] = 0
            elif ptype == "boolean":
                args[name] = False
            elif ptype == "object":
                args[name] = {}
            elif ptype == "array":
                args[name] = []
            else:
                args[name] = "test"
    return args



def _detect_env_expansion(payload: str, response: dict) -> str:
    """
    Detect whether the server expanded an env var payload instead of echoing it.

    Returns "pass" or "warn".

    Decision tree:
      1. Error or isError → pass (input rejected)
      2. Literal payload echoed in response → pass (safe string echo)
      3. No text content → pass (not echoed at all)
      4. Path-like expansion pattern detected without literal payload → warn
      5. Otherwise → pass (content present but no expansion signature)
    """
    if "error" in response:
        return "pass"
    result = response.get("result", {})
    if result.get("isError"):
        return "pass"

    content_texts: list[str] = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            content_texts.append(item.get("text", ""))
    full_text = "\n".join(content_texts)

    if not full_text:
        return "pass"

    # Literal payload safely echoed
    if payload in full_text:
        return "pass"

    # Expansion indicators: Unix or Windows path patterns in the response
    if _UNIX_PATH_RE.search(full_text) or _WIN_PATH_RE.search(full_text):
        return "warn"

    return "pass"
