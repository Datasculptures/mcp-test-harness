"""
Known-good MCP server for test harness development.

Uses FastMCP (official SDK). Passes all conformance tests.
Three tools covering: single param, multi-param, optional param.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-good-server")


@mcp.tool()
def echo(message: str) -> str:
    """Echo the input message back."""
    return message


@mcp.tool()
def add(a: float, b: float) -> str:
    """Add two numbers and return the result as a string."""
    return str(a + b)


@mcp.tool()
def greet(name: str, greeting: str = "Hello") -> str:
    """Greet someone. The greeting parameter is optional."""
    return f"{greeting}, {name}!"


if __name__ == "__main__":
    mcp.run()
