"""
SDK-based STDIO client for happy-path conformance tests.

Wraps the official MCP Python SDK's ClientSession and stdio_client.
Used for tests where we want to verify server behaviour against a known-correct
client implementation — if the SDK client gets a valid response, the server is
conformant for that interaction.

NOT used for adversarial tests (malformed messages, security probes) — use
StdioRawClient for those.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool, Resource, Prompt


class StdioSdkClient:
    """
    SDK-backed MCP client for happy-path conformance tests.

    Use as an async context manager:
        async with StdioSdkClient(command) as client:
            tools = await client.list_tools()
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not isinstance(command, list) or not command:
            raise ValueError("command must be a non-empty list")
        self._command = command
        self._env = env
        self._timeout = timeout

        self._session: ClientSession | None = None
        self._capabilities: dict = {}
        self._server_info: dict = {}
        self._exit_stack: Any = None

    async def start(self) -> dict:
        """
        Launch the server and complete initialization.
        Returns the server capabilities dict from InitializeResult.
        """
        params = StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            env=self._env,
        )

        self._exit_stack = anyio.from_thread.BlockingPortal()

        # Use contextlib.AsyncExitStack to manage nested context managers
        import contextlib
        self._stack = contextlib.AsyncExitStack()

        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )

        result = await self._session.initialize()

        # Store capabilities and server info for callers
        caps = result.capabilities
        self._capabilities = {}
        if caps.tools is not None:
            self._capabilities["tools"] = (
                {"listChanged": caps.tools.listChanged}
                if caps.tools.listChanged is not None
                else {}
            )
        if caps.resources is not None:
            self._capabilities["resources"] = {}
        if caps.prompts is not None:
            self._capabilities["prompts"] = {}
        if caps.logging is not None:
            self._capabilities["logging"] = {}

        if result.serverInfo:
            self._server_info = {
                "name": result.serverInfo.name,
                "version": result.serverInfo.version,
            }

        return self._capabilities

    async def stop(self) -> None:
        """Clean shutdown — close the SDK session and subprocess."""
        if self._stack is not None:
            stack = self._stack
            self._stack = None
            self._session = None
            try:
                await stack.aclose()
            except BaseException:
                # anyio cancel scope / stream errors during teardown of bad servers
                pass

    async def __aenter__(self) -> "StdioSdkClient":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    @property
    def capabilities(self) -> dict:
        """Server capabilities dict from initialization."""
        return self._capabilities

    @property
    def server_info(self) -> dict:
        """Server info (name, version) from initialization."""
        return self._server_info

    async def list_tools(self) -> list[dict]:
        """Call tools/list and return the tool list as plain dicts."""
        if self._session is None:
            raise RuntimeError("Not started")
        response = await self._session.list_tools()
        return [_tool_to_dict(t) for t in response.tools]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call tools/call and return the result as a plain dict."""
        if self._session is None:
            raise RuntimeError("Not started")
        result = await self._session.call_tool(name, arguments)
        return {
            "content": [_content_to_dict(c) for c in result.content],
            "isError": result.isError or False,
        }

    async def list_resources(self) -> list[dict]:
        """Call resources/list and return resource descriptors as plain dicts."""
        if self._session is None:
            raise RuntimeError("Not started")
        response = await self._session.list_resources()
        return [_resource_to_dict(r) for r in response.resources]

    async def read_resource(self, uri: str) -> dict:
        """Call resources/read and return contents as a plain dict."""
        if self._session is None:
            raise RuntimeError("Not started")
        from mcp.types import AnyUrl
        result = await self._session.read_resource(AnyUrl(uri))
        return {
            "contents": [_resource_content_to_dict(c) for c in result.contents]
        }

    async def list_prompts(self) -> list[dict]:
        """Call prompts/list and return prompt descriptors as plain dicts."""
        if self._session is None:
            raise RuntimeError("Not started")
        response = await self._session.list_prompts()
        return [_prompt_to_dict(p) for p in response.prompts]


# ------------------------------------------------------------------ #
# SDK type → plain dict helpers
# ------------------------------------------------------------------ #

def _tool_to_dict(tool: Tool) -> dict:
    schema = tool.inputSchema
    if schema is None:
        schema_val = None
    elif isinstance(schema, dict):
        schema_val = schema
    else:
        schema_val = schema.model_dump()
    d: dict = {
        "name": tool.name,
        "inputSchema": schema_val,
    }
    if tool.description:
        d["description"] = tool.description
    return d


def _resource_to_dict(resource: Resource) -> dict:
    return {
        "uri": str(resource.uri),
        "name": resource.name,
        **({"description": resource.description} if resource.description else {}),
        **({"mimeType": resource.mimeType} if resource.mimeType else {}),
    }


def _resource_content_to_dict(content: Any) -> dict:
    d: dict = {"uri": str(content.uri)}
    if hasattr(content, "text"):
        d["text"] = content.text
    if hasattr(content, "blob"):
        d["blob"] = content.blob
    if content.mimeType:
        d["mimeType"] = content.mimeType
    return d


def _prompt_to_dict(prompt: Prompt) -> dict:
    d: dict = {"name": prompt.name}
    if prompt.description:
        d["description"] = prompt.description
    return d


def _content_to_dict(content: Any) -> dict:
    if hasattr(content, "type"):
        d: dict = {"type": content.type}
        if hasattr(content, "text"):
            d["text"] = content.text
        if hasattr(content, "data"):
            d["data"] = content.data
        if hasattr(content, "mimeType") and content.mimeType:
            d["mimeType"] = content.mimeType
        return d
    return {"raw": str(content)}
