from __future__ import annotations

import pytest

from app.db.session import init_db


@pytest.fixture()
def db(tmp_path):
    init_db(tmp_path / "test.sqlite3")
