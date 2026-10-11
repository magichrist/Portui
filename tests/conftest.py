"""Shared pytest fixtures. No test may require elevated privileges or a real PID."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SRC = Path(__file__).resolve().parents[1] / "src"


if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Screens auto-refresh every couple of seconds by default, which makes any test
# that drives the UI non-deterministic: the timer repopulates tables underneath
# the assertions. Tests opt back in explicitly with refresh_interval, or by
# pressing R to exercise the toggle itself.
from ports_explorer.config import build_settings, get_settings  # noqa: E402

build_settings(refresh_interval=0.0)


@pytest.fixture
def auto_update():
    """Opt a test into a live auto-refresh timer, restoring the default after.

    Yields the interval in seconds. Screens must be created *after* this fixture
    runs, because the timer is built when the screen mounts.
    """
    original = get_settings().refresh_interval
    interval = 0.25
    build_settings(refresh_interval=interval)
    try:
        yield interval
    finally:
        build_settings(refresh_interval=original)


def load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def mixed_output() -> str:
    return load_fixture("lsof_mixed.txt")


@pytest.fixture
def states_output() -> str:
    return load_fixture("lsof_states.txt")


@pytest.fixture
def duplicate_fds_output() -> str:
    return load_fixture("lsof_duplicate_fds.txt")


@pytest.fixture
def multi_process_output() -> str:
    return load_fixture("lsof_multi_process.txt")


@pytest.fixture
def state_boundaries_output() -> str:
    return load_fixture("lsof_state_boundaries.txt")


@pytest.fixture
def empty_output() -> str:
    return load_fixture("lsof_empty.txt")
