"""
JSON-RPC 2.0 message construction and parsing helpers.

Pure functions — no I/O, no side effects. All validation is structural.
Never eval() or exec() any content received from servers.
"""

from __future__ import annotations
from mcp_test_harness import __version__

MCP_PROTOCOL_VERSION = "2025-11-25"


def make_request(
    method: str,
    params: dict | None = None,
    id: int | str | None = None,
) -> dict:
    """Construct a JSON-RPC 2.0 request dict."""
    msg: dict = {"jsonrpc": "2.0", "id": id if id is not None else 1, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_notification(method: str, params: dict | None = None) -> dict:
    """Construct a JSON-RPC 2.0 notification dict (no id, no response expected)."""
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_initialize_request(id: int | str = 1) -> dict:
    """
    Construct an MCP initialize request targeting spec 2025-11-25.
    protocolVersion MUST be MCP_PROTOCOL_VERSION.
    """
    return make_request(
        method="initialize",
        params={
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
            },
            "clientInfo": {
                "name": "mcp-test-harness",
                "version": __version__,
            },
        },
        id=id,
    )


def make_initialized_notification() -> dict:
    """Construct the notifications/initialized notification."""
    return make_notification("notifications/initialized")


def parse_response(data: dict) -> tuple[str | int | None, dict | None, dict | None]:
    """
    Parse a JSON-RPC response dict into (id, result, error).

    Returns a tuple of:
        - id: the response id (may be None for parse errors)
        - result: the result dict if successful, else None
        - error: the error dict if failed, else None

    Does not raise — returns None values for missing fields.
    """
    resp_id = data.get("id")
    result = data.get("result")
    error = data.get("error")
    return resp_id, result, error


def is_valid_jsonrpc(data: dict) -> bool:
    """
    Check if a dict has structurally valid JSON-RPC 2.0 format.

    Valid means:
    - Has "jsonrpc" == "2.0"
    - Is a request (has "id" that is str/int, and "method" that is str), OR
    - Is a notification (has "method" that is str, no "id"), OR
    - Is a response (has "id" that is str/int, and exactly one of "result"/"error")
    """
    if not isinstance(data, dict):
        return False
    if data.get("jsonrpc") != "2.0":
        return False

    has_id = "id" in data
    has_method = "method" in data
    has_result = "result" in data
    has_error = "error" in data

    if has_method:
        if not isinstance(data["method"], str):
            return False
        if has_id:
            # Request: id must be str or int, not null
            if data["id"] is None:
                return False
            if not isinstance(data["id"], (str, int)):
                return False
        # Notification: no id required
        return True

    if not has_method:
        # Response: must have id (str/int, not null) and exactly one of result/error
        if not has_id:
            return False
        if data["id"] is None:
            return False
        if not isinstance(data["id"], (str, int)):
            return False
        if has_result == has_error:  # both True or both False
            return False
        return True

    return False


def is_error_response(data: dict) -> bool:
    """Return True if data is a JSON-RPC error response."""
    return isinstance(data, dict) and "error" in data and "result" not in data


def get_error_code(data: dict) -> int | None:
    """Extract the error code from an error response, or None."""
    error = data.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, int):
            return code
    return None


def get_result(data: dict) -> dict | None:
    """Extract the result from a success response, or None."""
    return data.get("result") if "error" not in data else None
