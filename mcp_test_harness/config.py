"""Server configuration for the test harness."""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ServerConfig:
    """Configuration for the MCP server under test."""

    command: list[str]
    """Server launch command as an explicit list. Never a string — shell=False enforced."""

    transport: str = "stdio"
    """Transport type. 'stdio' for Phase 1; 'http' added in Phase 3."""

    timeout: float = 10.0
    """Default timeout in seconds for all server communications."""

    env: dict[str, str] | None = None
    """Optional explicit environment for the subprocess.
    If None, a safe minimal env is constructed at launch time.
    Never inherited wholesale from the parent process."""

    max_message_size: int = 10 * 1024 * 1024
    """Maximum message size in bytes (default 10 MB).
    Messages exceeding this size raise MessageTooLarge."""

    def __post_init__(self) -> None:
        if not isinstance(self.command, list):
            raise ValueError(
                f"ServerConfig.command must be a list, got {type(self.command).__name__}. "
                "Never pass a shell string — use an explicit argument list."
            )
        if not self.command:
            raise ValueError("ServerConfig.command must not be empty.")
