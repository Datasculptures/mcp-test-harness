"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOOD_SERVER_PATH = FIXTURES_DIR / "good_server.py"
BAD_SERVER_PATH = FIXTURES_DIR / "bad_server.py"


@pytest.fixture
def good_server_cmd() -> list[str]:
    return [sys.executable, str(GOOD_SERVER_PATH)]


@pytest.fixture
def bad_server_cmd() -> list[str]:
    return [sys.executable, str(BAD_SERVER_PATH)]
