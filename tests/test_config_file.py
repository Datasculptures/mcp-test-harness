"""Unit tests for config file loading and merging."""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import pytest

from mcp_test_harness.config_file import load_config_file, merge_config


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".mcp-test-harness.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ------------------------------------------------------------------ #
# load_config_file
# ------------------------------------------------------------------ #

def test_load_valid_config(tmp_path):
    p = _write_yaml(tmp_path, """
        command:
          - python
          - -m
          - my_server
        timeout: 20
        format: json
        verbose: true
        suites:
          - conformance
          - security
    """)
    cfg = load_config_file(str(p))
    assert cfg["command"] == ["python", "-m", "my_server"]
    assert cfg["timeout"] == 20
    assert cfg["format"] == "json"
    assert cfg["verbose"] is True
    assert cfg["suites"] == ["conformance", "security"]


def test_load_missing_config(tmp_path):
    missing = str(tmp_path / "nonexistent.yaml")
    with pytest.raises(FileNotFoundError):
        load_config_file(missing)


def test_load_empty_file_returns_empty_dict(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    cfg = load_config_file(str(p))
    assert cfg == {}


def test_load_invalid_yaml_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(": : : invalid yaml ::: {[}", encoding="utf-8")
    with pytest.raises(Exception):
        load_config_file(str(p))


def test_load_non_mapping_raises(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- foo\n- bar\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_config_file(str(p))


def test_config_string_command_rejected(tmp_path):
    p = _write_yaml(tmp_path, 'command: "python server.py"\n')
    with pytest.raises(ValueError, match="list"):
        load_config_file(str(p))


def test_config_empty_command_rejected(tmp_path):
    p = _write_yaml(tmp_path, "command: []\n")
    with pytest.raises(ValueError, match="empty"):
        load_config_file(str(p))


def test_config_invalid_format_rejected(tmp_path):
    p = _write_yaml(tmp_path, "format: xml\n")
    with pytest.raises(ValueError, match="format"):
        load_config_file(str(p))


def test_config_invalid_suite_rejected(tmp_path):
    p = _write_yaml(tmp_path, "suites:\n  - unknown_suite\n")
    with pytest.raises(ValueError, match="suite"):
        load_config_file(str(p))


def test_config_negative_timeout_rejected(tmp_path):
    p = _write_yaml(tmp_path, "timeout: -5\n")
    with pytest.raises(ValueError, match="timeout"):
        load_config_file(str(p))


def test_auto_discovery_missing_returns_empty(tmp_path, monkeypatch):
    """Auto-discovery returns empty dict when no config in cwd."""
    monkeypatch.chdir(tmp_path)
    cfg = load_config_file(None)
    assert cfg == {}


def test_auto_discovery_found_prints_notice(tmp_path, monkeypatch, capsys):
    """Auto-discovery prints a notice to stderr."""
    monkeypatch.chdir(tmp_path)
    _write_yaml(tmp_path, "timeout: 5\n")
    cfg = load_config_file(None)
    assert cfg["timeout"] == 5
    captured = capsys.readouterr()
    assert ".mcp-test-harness.yaml" in captured.err


# ------------------------------------------------------------------ #
# merge_config
# ------------------------------------------------------------------ #

def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "command": [],
        "suite": None,
        "format": "text",
        "timeout": 10.0,
        "output": None,
        "verbose": False,
        "badge": False,
        "config": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_cli_overrides_config_timeout():
    args = _make_args(timeout=5.0)
    merge_config(args, {"timeout": 20})
    # CLI value 5.0 ≠ default 10.0, so config should NOT override it
    # But merge_config checks "if args.timeout == 10.0" to detect default
    # Here args.timeout=5.0 so it was explicitly set — config must not override
    assert args.timeout == 5.0


def test_command_from_config():
    args = _make_args(command=[])
    merge_config(args, {"command": ["python", "server.py"]})
    assert args.command == ["python", "server.py"]


def test_cli_command_not_overridden_by_config():
    args = _make_args(command=["python", "my_server.py"])
    merge_config(args, {"command": ["python", "other_server.py"]})
    assert args.command == ["python", "my_server.py"]


def test_suite_from_config():
    args = _make_args(suite=None)
    merge_config(args, {"suites": ["conformance"]})
    assert args.suite == ["conformance"]


def test_format_from_config():
    args = _make_args(format="text")
    merge_config(args, {"format": "json"})
    assert args.format == "json"


def test_verbose_from_config():
    args = _make_args(verbose=False)
    merge_config(args, {"verbose": True})
    assert args.verbose is True


def test_output_from_config(tmp_path):
    args = _make_args(output=None)
    merge_config(args, {"output": str(tmp_path / "report.json")})
    assert args.output is not None
