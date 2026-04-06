"""
Deliberately broken MCP server for negative test validation.

Built incrementally — each bad behaviour is added when the corresponding
test is written. This server exists to prove the harness detects real failures.

Current bad behaviours:
  [init]       Responds with wrong protocolVersion ("1999-01-01")
  [tools/list] Returns a tool whose inputSchema is null (violates spec)
  [tools/list] Returns a tool whose name contains a space (violates naming rules)
  [tools/call] "echo_unsafe" echoes raw argument without sanitisation (injection canary)
  [tools/call] "run_command" simulates a shell-injection-vulnerable tool:
               returns just the canary string when it detects injection payload,
               simulating what a vulnerable server that executes shell commands would do
  [tools/call] "no_validation" accepts any input without type or required-field checking
  [tools/call] "read_file" simulates a path-traversal-vulnerable file reader:
               returns passwd-like content when it detects traversal patterns in the path
  [tools/call] "hang" sleeps indefinitely — tests harness timeout handling
  [errors]     Error responses include a fake stack trace (info disclosure, unix path)
  [stdout]     Emits one non-JSON line to stdout after init (purity violation)

Usage: invoked as a subprocess by the test harness. Reads JSON-RPC from stdin,
writes responses to stdout. Deliberately does NOT use FastMCP.

Security note: this server is intentionally broken and unsafe by design.
It must only be launched by the test harness in a controlled test context.
It performs no real file I/O, no network I/O, and no shell execution.
"""

from __future__ import annotations

import json
import sys
import time


def write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def write_raw(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


CANARY = "HARNESS_CANARY_7f3a9b"

BAD_TOOLS = [
    {
        "name": "echo_unsafe",
        "description": "Echo tool that skips input sanitisation.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        # Simulates a shell-injection-vulnerable tool.
        # Real vulnerability: server passes args to subprocess/shell unsanitised.
        # Simulated here: if we detect a canary injection payload, return just the
        # canary string (as a real shell-executing server would after `echo CANARY`
        # runs). No actual shell execution occurs — this server is safe.
        "name": "run_command",
        "description": "Simulates a command-running tool vulnerable to shell injection.",
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        # Accepts any input with no type checking or required-field enforcement.
        "name": "no_validation",
        "description": "Tool that skips all input validation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["value", "count"],
        },
    },
    {
        "name": "null_schema_tool",
        "description": "Tool with null inputSchema — violates spec.",
        "inputSchema": None,  # MUST NOT be null per spec
    },
    {
        "name": "bad name with spaces",
        "description": "Tool name violates naming rules.",
        "inputSchema": {"type": "object"},
    },
    {
        # Simulates a path-traversal-vulnerable file reader.
        # Returns fake passwd content when traversal patterns are detected in path.
        # No actual file I/O occurs — this is purely simulated.
        "name": "read_file",
        "description": "Simulates a file-reading tool vulnerable to path traversal.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        # Hangs indefinitely — tests harness timeout handling.
        # Uses an integer argument so it is not selected by string-arg-seeking tests.
        "name": "hang",
        "description": "Sleeps indefinitely to test harness timeout handling.",
        "inputSchema": {
            "type": "object",
            "properties": {"duration": {"type": "integer"}},
            "required": [],
        },
    },
]

# Traversal indicators that trigger the fake passwd response in read_file
_TRAVERSAL_PATHS = [
    "etc/passwd", "etc/shadow", "etc/hosts",
    "../", "..\\",
    "%2e%2e", "%2f",
    "\x00",
]

# Fake passwd content that the path_traversal suite will detect
_FAKE_PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
)


def handle(msg: dict) -> None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        # Bad behaviour: wrong protocolVersion
        write({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "1999-01-01",  # wrong version
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bad-server", "version": "0.0.0"},
            },
        })
        # Bad behaviour: emit non-JSON to stdout after init response
        write_raw("BAD_SERVER_STARTUP_MESSAGE_NOT_JSON")

    elif method == "notifications/initialized":
        pass  # notifications have no response

    elif method == "ping":
        if msg_id is not None:
            write({"jsonrpc": "2.0", "id": msg_id, "result": {}})

    elif method == "tools/list":
        write({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": BAD_TOOLS},
        })

    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if name == "echo_unsafe":
            # Bad behaviour: echo raw argument without any sanitisation.
            # An injection canary will appear in the output as-is.
            raw_input = arguments.get("message", "")
            write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": raw_input}],
                    "isError": False,
                },
            })

        elif name == "run_command":
            # Simulates a shell-injection-vulnerable tool.
            # If the argument contains a canary injection payload, return ONLY the
            # canary string — simulating what a real server would return after the
            # shell metacharacter caused `echo CANARY` to execute separately.
            # No actual shell execution occurs in this server.
            cmd_arg = arguments.get("command", "")
            if CANARY in cmd_arg:
                # Return just the canary — as if the shell ran `echo CANARY` separately
                output = CANARY
            else:
                output = f"ran: {cmd_arg}"
            write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": output}],
                    "isError": False,
                },
            })

        elif name == "no_validation":
            # Bad behaviour: accept any input without type or required-field checking.
            # Returns a fixed response regardless of what was passed.
            write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": "ok"}],
                    "isError": False,
                },
            })

        elif name == "read_file":
            # Simulates a path-traversal-vulnerable file reader.
            # Returns fake passwd content when traversal patterns are detected.
            path_arg = str(arguments.get("path", ""))
            path_lower = path_arg.lower()
            is_traversal = any(t in path_lower for t in _TRAVERSAL_PATHS)
            if is_traversal:
                content = _FAKE_PASSWD
            else:
                content = f"contents of: {path_arg}"
            write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": content}],
                    "isError": False,
                },
            })

        elif name == "hang":
            # Bad behaviour: sleep indefinitely to test harness timeout.
            # The harness should kill this subprocess via its timeout mechanism.
            time.sleep(300)
            # If somehow woken (e.g., SIGTERM not caught), send a response anyway.
            write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": "hang complete"}],
                    "isError": False,
                },
            })

        else:
            # Bad behaviour: error response leaks a fake stack trace with a unix path
            write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32602,
                    "message": "Unknown tool",
                    "data": (
                        "Traceback (most recent call last):\n"
                        '  File "/home/user/bad_server.py", line 42, in handle\n'
                        "    raise ValueError('unknown tool')\n"
                        "ValueError: unknown tool"
                    ),
                },
            })
    else:
        # Unknown method — return MethodNotFound (this part is correct)
        if msg_id is not None:
            write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            # Can't respond to unparseable input without an id; skip
            continue
        handle(msg)


if __name__ == "__main__":
    main()
