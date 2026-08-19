"""_ensure_user раньше не сообщал, новый пользователь или нет — cmd_start
определял первый /start по in-memory bot_data["known_users"], которое
обнуляется при каждом рестарте бота: вернувшийся пользователь после
любого редеплоя снова видел приветственный текст с кредитом автора.
Теперь источник истины — сама таблица User, которая переживает рестарт."""

from __future__ import annotations

from app.bot.handlers.start import _ensure_user
from app.db.models import User
from app.db.session import get_session


def test_ensure_user_returns_true_for_brand_new_user(db):
    is_new = _ensure_user(111, "Alice", False)

    assert is_new is True
    with get_session() as session:
        assert session.query(User).filter_by(tg_id=111).count() == 1


def test_ensure_user_returns_false_for_returning_user(db):
    _ensure_user(111, "Alice", False)

    is_new_second_call = _ensure_user(111, "Alice", False)

    assert is_new_second_call is False


def test_ensure_user_survives_simulated_restart(db):
    """Ключевой сценарий бага: 'рестарт' — это просто новый вызов без
    какого-либо in-memory состояния, только БД. Второй /start того же
    юзера не должен снова считаться первым."""
    first_call = _ensure_user(42, "Bob", False)
    # ничего похожего на bot_data["known_users"] здесь нет и не должно быть
    second_call_after_restart = _ensure_user(42, "Bob", False)

    assert first_call is True
    assert second_call_after_restart is False
