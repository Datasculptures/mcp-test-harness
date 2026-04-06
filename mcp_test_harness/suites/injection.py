"""
Shell injection probe suite.

Sends shell metacharacter payloads as tool arguments and detects whether
the server executed a shell command or safely echoed/rejected the input.
Also probes for environment variable expansion in tool arguments.

Uses the HARNESS_CANARY_7f3a9b string to distinguish execution from echo:
  - Canary returned WITH metachar prefix → safe echo, pass
  - Canary returned WITHOUT metachar prefix → shell likely executed it, fail
  - Server rejected the input → pass
  - Ambiguous → warn

Env var probes check whether the server expands variables like ${HOME}
instead of treating them as opaque strings.

All tests use the raw client and run within a single server process.
Payloads are hardcoded constants — never derived from external input.
No shell=True, no eval, no subprocess in this module.
"""

from __future__ import annotations

import asyncio
import re
import time

from mcp_test_harness.client.stdio_raw import ReadTimeout, StdioRawClient
from mcp_test_harness.config import ServerConfig
from mcp_test_harness.suites.base import BaseSuite, TestResult

# Canary must be unique enough that it cannot appear in normal server output.
CANARY = "HARNESS_CANARY_7f3a9b"

# (payload_string, short_name, metachar_prefix_that_must_survive_safe_echo)
INJECTION_PAYLOADS: list[tuple[str, str, str]] = [
    ("; echo " + CANARY,        "semicolon",     ";"),
    ("| echo " + CANARY,        "pipe",          "|"),
    ("`echo " + CANARY + "`",   "backtick",      "`"),
    ("$(echo " + CANARY + ")",  "dollar_paren",  "$("),
    ("&& echo " + CANARY,       "ampersand",     "&&"),
]

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
        t0_total = time.monotonic()
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

                for payload, payload_name, metachar in INJECTION_PAYLOADS:
                    for tool_info in string_tools:
                        test_name = f"shell_metachar_{payload_name}__{tool_info['name']}"
                        t0 = time.monotonic()
                        result = await self._run_one(
                            client, config, test_name, tool_info, payload, metachar
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
        metachar: str,
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
                "server timed out on injection payload — may have crashed or hung",
            )
        except asyncio.CancelledError:
            return self._error(test_name, "test cancelled")

        status = _detect_injection(payload, metachar, response)
        if status == "pass":
            return self._pass(test_name)
        if status == "fail":
            return self._fail(
                test_name,
                f"canary detected without metachar prefix '{metachar}' — "
                "possible shell command execution",
            )
        if status == "warn":
            return self._warn(test_name, "ambiguous canary detection in response")
        # status == "error" (shouldn't happen, but be safe)
        return self._error(test_name, "unexpected detection result")

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


def _detect_injection(payload: str, metachar_prefix: str, response: dict) -> str:
    """
    Analyse a tool response for injection indicators.

    Returns one of: "pass", "fail", "warn", "error"

    Decision tree:
      1. Server returned JSON-RPC error or isError:true → pass (input rejected)
      2. Canary not in response text at all → pass (not echoed, not executed)
      3. Full payload (including metachar) echoed back → pass (safe string echo)
      4. Canary present but metachar prefix absent → fail (shell stripped it)
      5. Canary present and metachar also present (ambiguous position) → warn
    """
    # Case 1: input was rejected
    if "error" in response:
        return "pass"
    result = response.get("result", {})
    if result.get("isError"):
        return "pass"

    # Extract all text content
    content_texts: list[str] = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            content_texts.append(item.get("text", ""))
    full_text = "\n".join(content_texts)

    # Case 2: canary not present at all
    if CANARY not in full_text:
        return "pass"

    # Case 3: full payload (with metachar) echoed safely
    if payload in full_text:
        return "pass"

    # Case 4: canary present but metachar prefix is gone → likely executed
    if metachar_prefix not in full_text:
        return "fail"

    # Case 5: canary and metachar both present but payload string not exact
    return "warn"


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
