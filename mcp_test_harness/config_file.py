"""
Configuration file loading for mcp-test-harness.

Supports .mcp-test-harness.yaml in the working directory (auto-discovery)
or a path specified via --config.

Precedence: CLI arguments > config file values > argparse defaults.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

DEFAULT_CONFIG_NAME = ".mcp-test-harness.yaml"

# Keys the config file may set, and their expected types for basic validation
_KNOWN_KEYS: dict[str, type | tuple] = {
    "command":   list,
    "transport": str,
    "suites":    list,
    "timeout":   (int, float),
    "format":    str,
    "output":    str,
    "verbose":   bool,
}

_VALID_FORMATS = {"text", "json", "markdown"}
_VALID_SUITES  = {"conformance", "security", "operational", "all"}
_VALID_TRANSPORTS = {"stdio"}


def load_config_file(path: str | None = None) -> dict:
    """
    Load config from YAML file. Returns empty dict if file not found.

    If path is None, looks for DEFAULT_CONFIG_NAME in the current directory.
    Prints a notice to stderr when auto-discovering a config file.

    Raises ValueError for structural problems (e.g. command is a string).
    """
    if not _YAML_AVAILABLE:
        # pyyaml not installed — silently skip config file loading
        return {}

    if path:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
    else:
        config_path = Path.cwd() / DEFAULT_CONFIG_NAME
        if not config_path.exists():
            return {}
        print(f"Using config: {DEFAULT_CONFIG_NAME}", file=sys.stderr)

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file must be a YAML mapping, got {type(raw).__name__}"
        )

    _validate_config(raw, config_path)
    return raw


def _validate_config(data: dict, path: Path) -> None:
    """Raise ValueError for invalid config values."""
    if "command" in data:
        if not isinstance(data["command"], list):
            raise ValueError(
                f"{path}: 'command' must be a list (e.g. [python, -m, my_server]), "
                f"got {type(data['command']).__name__!r}"
            )
        if not data["command"]:
            raise ValueError(f"{path}: 'command' list must not be empty")

    if "format" in data and data["format"] not in _VALID_FORMATS:
        raise ValueError(
            f"{path}: 'format' must be one of {sorted(_VALID_FORMATS)}, "
            f"got {data['format']!r}"
        )

    if "suites" in data:
        if not isinstance(data["suites"], list):
            raise ValueError(f"{path}: 'suites' must be a list")
        for s in data["suites"]:
            if s not in _VALID_SUITES:
                raise ValueError(
                    f"{path}: unknown suite {s!r}; "
                    f"valid values: {sorted(_VALID_SUITES)}"
                )

    if "transport" in data and data["transport"] not in _VALID_TRANSPORTS:
        raise ValueError(
            f"{path}: 'transport' must be one of {sorted(_VALID_TRANSPORTS)}, "
            f"got {data['transport']!r}"
        )

    if "timeout" in data:
        if not isinstance(data["timeout"], (int, float)) or data["timeout"] <= 0:
            raise ValueError(
                f"{path}: 'timeout' must be a positive number, "
                f"got {data['timeout']!r}"
            )


def merge_config(args, file_config: dict) -> None:
    """
    Merge file_config into args in-place. CLI args take precedence.

    Only sets an attribute from file_config when the CLI value matches the
    argparse default (i.e. the user didn't explicitly pass it).
    """
    # command: CLI provides via args.command (positional REMAINDER after --)
    # If CLI command is empty/missing but config has it, use config
    if not getattr(args, "command", None) and "command" in file_config:
        args.command = file_config["command"]

    # suite: CLI uses action="append" so default is None
    if getattr(args, "suite", None) is None and "suites" in file_config:
        args.suite = file_config["suites"]

    # format: default is "text"
    if getattr(args, "format", "text") == "text" and "format" in file_config:
        args.format = file_config["format"]

    # timeout: default is 10.0
    if getattr(args, "timeout", 10.0) == 10.0 and "timeout" in file_config:
        args.timeout = float(file_config["timeout"])

    # output: default is None
    if getattr(args, "output", None) is None and "output" in file_config:
        args.output = file_config["output"]

    # verbose: default is False
    if not getattr(args, "verbose", False) and file_config.get("verbose"):
        args.verbose = True
