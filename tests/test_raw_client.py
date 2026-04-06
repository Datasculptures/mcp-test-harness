"""
Unit tests for StdioRawClient.

Tests the raw client in isolation using simple subprocesses (Python one-liners),
not full MCP servers. This validates the transport layer before any suite code exists.
"""

from __future__ import annotations

import asyncio
import json
import sys
import pytest

from mcp_test_harness.client.stdio_raw import (
    StdioRawClient,
    MessageTooLarge,
    ReadTimeout,
)
from mcp_test_harness.client.protocol import (
    make_request,
    make_notification,
    make_initialize_request,
    make_initialized_notification,
    is_valid_jsonrpc,
    get_error_code,
)

PYTHON = sys.executable


# ------------------------------------------------------------------ #
# protocol.py unit tests (pure functions, no subprocess)
# ------------------------------------------------------------------ #


def test_make_request_structure():
    msg = make_request("ping", id=5)
    assert msg["jsonrpc"] == "2.0"
    assert msg["id"] == 5
    assert msg["method"] == "ping"
    assert "params" not in msg


def test_make_request_with_params():
    msg = make_request("tools/call", params={"name": "echo"}, id=1)
    assert msg["params"] == {"name": "echo"}


def test_make_notification_has_no_id():
    msg = make_notification("notifications/initialized")
    assert "id" not in msg
    assert msg["jsonrpc"] == "2.0"
    assert msg["method"] == "notifications/initialized"


def test_make_initialize_request():
    msg = make_initialize_request(id=1)
    assert msg["method"] == "initialize"
    assert msg["params"]["protocolVersion"] == "2025-11-25"
    assert "clientInfo" in msg["params"]
    assert msg["params"]["clientInfo"]["name"] == "mcp-test-harness"


def test_is_valid_jsonrpc_request():
    assert is_valid_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})


def test_is_valid_jsonrpc_notification():
    assert is_valid_jsonrpc({"jsonrpc": "2.0", "method": "notifications/foo"})


def test_is_valid_jsonrpc_success_response():
    assert is_valid_jsonrpc({"jsonrpc": "2.0", "id": 1, "result": {}})


def test_is_valid_jsonrpc_error_response():
    assert is_valid_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "not found"}}
    )


def test_is_valid_jsonrpc_null_id_rejected():
    # MCP prohibits null id
    assert not is_valid_jsonrpc({"jsonrpc": "2.0", "id": None, "method": "ping"})


def test_is_valid_jsonrpc_both_result_and_error_rejected():
    assert not is_valid_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": -32600, "message": "x"}}
    )


def test_is_valid_jsonrpc_neither_result_nor_error_rejected():
    assert not is_valid_jsonrpc({"jsonrpc": "2.0", "id": 1})


def test_is_valid_jsonrpc_wrong_version():
    assert not is_valid_jsonrpc({"jsonrpc": "1.0", "id": 1, "method": "ping"})


def test_get_error_code():
    response = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "m"}}
    assert get_error_code(response) == -32601


# ------------------------------------------------------------------ #
# StdioRawClient — string command rejected
# ------------------------------------------------------------------ #


def test_string_command_raises():
    with pytest.raises(ValueError, match="must be a list"):
        StdioRawClient("python -m something")  # type: ignore[arg-type]


def test_empty_command_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        StdioRawClient([])


# ------------------------------------------------------------------ #
# StdioRawClient — subprocess lifecycle
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_start_and_stop():
    """Client can launch and cleanly stop a subprocess."""
    # Use a Python process that just reads stdin and exits on EOF
    client = StdioRawClient([PYTHON, "-c", "import sys; sys.stdin.read()"])
    await client.start()
    assert client.returncode is None  # still running
    await client.stop()
    # After stop, process should be gone
    await asyncio.sleep(0.1)
    assert client.returncode is not None


@pytest.mark.asyncio
async def test_send_raw_and_read_message():
    """send_raw writes bytes; read_message reads the echoed JSON back."""
    # A subprocess that echoes each stdin line to stdout
    echo_script = "import sys\nfor line in sys.stdin:\n    sys.stdout.write(line)\n    sys.stdout.flush()"
    client = StdioRawClient([PYTHON, "-c", echo_script])
    await client.start()

    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    await client.send_raw(json.dumps(msg).encode())
    result = await client.read_message(timeout=3.0)
    assert result == msg

    await client.stop()


@pytest.mark.asyncio
async def test_read_message_timeout():
    """read_message raises ReadTimeout when no data arrives."""
    # A subprocess that does nothing
    client = StdioRawClient([PYTHON, "-c", "import time; time.sleep(30)"])
    await client.start()
    with pytest.raises(ReadTimeout):
        await client.read_message(timeout=0.3)
    await client.stop()


@pytest.mark.asyncio
async def test_send_request_and_response():
    """send_request dispatches by id and returns the matching response."""
    # A script that reads a JSON line and echoes it back (simulates a simple server)
    responder = (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    if 'method' in msg and 'id' in msg:\n"
        "        resp = {'jsonrpc': '2.0', 'id': msg['id'], 'result': {'pong': True}}\n"
        "        sys.stdout.write(json.dumps(resp) + '\\n')\n"
        "        sys.stdout.flush()\n"
    )
    client = StdioRawClient([PYTHON, "-c", responder])
    await client.start()

    response = await client.send_request("ping", id=42)
    assert response["id"] == 42
    assert response["result"] == {"pong": True}

    await client.stop()


@pytest.mark.asyncio
async def test_message_size_cap_on_outgoing():
    """send_raw raises MessageTooLarge for oversized outgoing data."""
    client = StdioRawClient(
        [PYTHON, "-c", "import sys; sys.stdin.read()"],
        max_message_size=100,
    )
    await client.start()
    oversized = b"x" * 200
    with pytest.raises(MessageTooLarge):
        await client.send_raw(oversized)
    await client.stop()


@pytest.mark.asyncio
async def test_all_stdout_lines_captured():
    """all_stdout_lines includes every line written to stdout."""
    script = (
        "import sys, json\n"
        "for i in range(3):\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': i, 'result': {}}) + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    client = StdioRawClient([PYTHON, "-c", script])
    await client.start()
    await asyncio.sleep(0.5)
    await client.stop()
    assert len(client.all_stdout_lines) == 3


@pytest.mark.asyncio
async def test_context_manager():
    """StdioRawClient works as an async context manager."""
    script = "import sys; sys.stdin.read()"
    async with StdioRawClient([PYTHON, "-c", script]) as client:
        assert client.returncode is None
    await asyncio.sleep(0.1)
    assert client.returncode is not None
