import pytest

from ai_check_bot.db import make_session_factory
from ai_check_bot.models import AIAccount


@pytest.fixture
def session_factory(tmp_path):
    return make_session_factory(tmp_path / "test.db")


@pytest.fixture
def account(session_factory):
    with session_factory() as session:
        acc = AIAccount(provider="fake", label="acc1", api_key="k")
        session.add(acc)
        session.commit()
        session.refresh(acc)
        return acc
