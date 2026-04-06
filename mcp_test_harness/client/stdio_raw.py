"""
Thin raw STDIO client for adversarial MCP testing.

Wraps asyncio subprocess I/O to give full control over the wire format.
Used for: malformed message tests, error handling tests, security probes.

Security requirements enforced here:
- command MUST be a list (raises ValueError for strings)
- shell=False always
- Explicit minimal environment (never full parent env)
- Hard timeouts on all reads
- Message size cap (default 10 MB)
- Windows: process.terminate() before process.kill() (no SIGTERM)
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
from typing import Any

from mcp_test_harness.client.protocol import (
    make_initialize_request,
    make_initialized_notification,
)


class MessageTooLarge(Exception):
    """Raised when a server message exceeds the configured size cap."""


class ReadTimeout(Exception):
    """Raised when a read operation times out."""


class ServerNotStarted(Exception):
    """Raised when attempting to use the client before calling start()."""


def _safe_env() -> dict[str, str]:
    """
    Build a minimal safe environment for the server subprocess.
    Mirrors the MCP SDK's approach: include only essential variables.
    Never inherit the full parent environment.
    """
    env: dict[str, str] = {}
    if platform.system() == "Windows":
        for key in ("APPDATA", "USERPROFILE", "TEMP", "TMP", "SYSTEMROOT",
                    "WINDIR", "COMSPEC", "PATH"):
            if key in os.environ:
                env[key] = os.environ[key]
    else:
        for key in ("PATH", "HOME", "SHELL", "LANG", "LC_ALL", "TMPDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


class StdioRawClient:
    """
    Low-level STDIO MCP client for adversarial testing.

    Provides both high-level (send_request, send_notification) and
    low-level (send_raw) access to the server's stdin/stdout.

    Not thread-safe. Use within a single asyncio task.
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        timeout: float = 10.0,
        max_message_size: int = 10 * 1024 * 1024,
    ) -> None:
        if not isinstance(command, list):
            raise ValueError(
                f"command must be a list of strings, got {type(command).__name__}. "
                "Shell strings are not accepted — use an explicit argument list."
            )
        if not command:
            raise ValueError("command must not be empty.")

        self._command = command
        self._env = env if env is not None else _safe_env()
        self._timeout = timeout
        self._max_message_size = max_message_size

        self._process: asyncio.subprocess.Process | None = None
        self._id_counter = 0
        self._pending: dict[int | str, asyncio.Future[dict]] = {}
        self._reader_task: asyncio.Task | None = None
        self._all_stdout_lines: list[str] = []
        # Unmatched messages (notifications, server-initiated requests) queue.
        # read_message() drains from here; background reader pushes to here.
        self._unmatched: asyncio.Queue[dict] = asyncio.Queue()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Launch the server subprocess and begin reading stdout."""
        self._unmatched = asyncio.Queue()
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        self._reader_task = asyncio.create_task(self._stdout_reader())

    async def stop(self) -> None:
        """
        Graceful shutdown: close stdin → wait 2s → terminate → wait 1s → kill.
        On Windows, terminate() maps to TerminateProcess (no SIGTERM).
        """
        if self._process is None:
            return

        # Cancel reader task first
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await asyncio.wait_for(self._reader_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Reject all pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

        if self._process.stdin:
            try:
                self._process.stdin.close()
                await asyncio.wait_for(self._process.stdin.wait_closed(), timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                pass

        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
            return
        except asyncio.TimeoutError:
            pass

        # Terminate (SIGTERM on Unix, TerminateProcess on Windows)
        try:
            self._process.terminate()
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(self._process.wait(), timeout=1.0)
            return
        except asyncio.TimeoutError:
            pass

        # Force kill
        try:
            self._process.kill()
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass

    async def __aenter__(self) -> "StdioRawClient":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    async def _write_line(self, data: bytes) -> None:
        """Write data + newline to stdin. Enforces max message size."""
        if len(data) > self._max_message_size:
            raise MessageTooLarge(
                f"Outgoing message size {len(data)} exceeds cap {self._max_message_size}"
            )
        if self._process is None or self._process.stdin is None:
            raise ServerNotStarted("Server not started. Call start() first.")
        self._process.stdin.write(data + b"\n")
        await self._process.stdin.drain()

    async def send_request(
        self,
        method: str,
        params: dict | None = None,
        id: int | str | None = None,
    ) -> dict:
        """
        Send a JSON-RPC request and wait for the matching response.
        Auto-assigns id if None.
        """
        if id is None:
            id = self._next_id()

        msg = {"jsonrpc": "2.0", "id": id, "method": method}
        if params is not None:
            msg["params"] = params

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._pending[id] = fut

        try:
            await self._write_line(json.dumps(msg).encode())
        except Exception:
            self._pending.pop(id, None)
            raise

        try:
            return await asyncio.wait_for(fut, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(id, None)
            raise ReadTimeout(
                f"No response to request id={id} method={method!r} "
                f"within {self._timeout}s"
            )

    async def send_notification(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._write_line(json.dumps(msg).encode())

    async def send_raw(self, data: bytes) -> None:
        """
        Write raw bytes to stdin. For malformed message tests.
        No JSON validation — caller is responsible for content.
        Still enforces max_message_size.
        """
        await self._write_line(data)

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    async def _stdout_reader(self) -> None:
        """Background task: read lines from stdout and dispatch to waiters."""
        assert self._process and self._process.stdout
        while True:
            try:
                line = await self._process.stdout.readline()
            except Exception:
                break
            if not line:
                break

            if len(line) > self._max_message_size:
                # Drop oversized messages; don't crash the reader
                continue

            raw = line.decode(errors="replace").rstrip("\n\r")
            self._all_stdout_lines.append(raw)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Non-JSON line — record it but don't dispatch (stdout purity test sees it)
                continue

            if not isinstance(data, dict):
                continue

            resp_id = data.get("id")
            if resp_id is not None and resp_id in self._pending:
                fut = self._pending.pop(resp_id)
                if not fut.done():
                    fut.set_result(data)
            else:
                # Unmatched: notification, server-initiated request, or unknown response.
                # Push to queue for read_message() consumers.
                try:
                    self._unmatched.put_nowait(data)
                except asyncio.QueueFull:
                    pass  # drop if consumer isn't keeping up

    async def read_message(self, timeout: float | None = None) -> dict:
        """
        Return the next unmatched JSON-RPC message from the server.
        Unmatched messages are notifications, server-initiated requests, or
        responses that didn't match a pending send_request() id.

        Raises ReadTimeout if no message arrives within timeout seconds.
        Safe to call concurrently with send_request() — does not touch stdout directly.
        """
        if self._process is None:
            raise ServerNotStarted("Server not started.")

        effective_timeout = timeout if timeout is not None else self._timeout
        try:
            return await asyncio.wait_for(
                self._unmatched.get(), timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            raise ReadTimeout(f"No unmatched message from server within {effective_timeout}s")

    async def read_stderr(self) -> str:
        """Read all available stderr output (non-blocking, best effort)."""
        if self._process is None or self._process.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(
                self._process.stderr.read(65536), timeout=0.1
            )
            return data.decode(errors="replace")
        except asyncio.TimeoutError:
            return ""

    @property
    def all_stdout_lines(self) -> list[str]:
        """All stdout lines seen since start(), including non-JSON lines."""
        return list(self._all_stdout_lines)

    @property
    def returncode(self) -> int | None:
        """Server process return code, or None if still running."""
        if self._process is None:
            return None
        return self._process.returncode

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #

    async def initialize(self) -> dict:
        """
        Send initialize request + initialized notification.
        Returns the InitializeResult dict (the 'result' field of the response).
        Raises on error response or timeout.
        """
        request = make_initialize_request(id=self._next_id())
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[request["id"]] = fut

        await self._write_line(json.dumps(request).encode())
        try:
            response = await asyncio.wait_for(fut, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request["id"], None)
            raise ReadTimeout(f"No response to initialize within {self._timeout}s")

        if "error" in response:
            raise RuntimeError(f"initialize failed: {response['error']}")

        notification = make_initialized_notification()
        await self._write_line(json.dumps(notification).encode())

        return response.get("result", {})
