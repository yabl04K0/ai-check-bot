from __future__ import annotations

import pytest

from app.db.session import init_db
from app.providers import circuit_breaker, claude_code_usage


@pytest.fixture()
def db(tmp_path):
    init_db(tmp_path / "test.sqlite3")


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


@pytest.fixture(autouse=True)
def _reset_claude_code_usage_cache():
    claude_code_usage.reset_cache()
    yield
    claude_code_usage.reset_cache()
