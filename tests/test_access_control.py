"""is_authorized() — единственная проверка, решающая, пускать ли юзера к
любому хендлеру бота (см. app/bot/access_control.py). Тестируем на
дак-тайпинге вместо настоящих Update/Context — функция трогает только
update.effective_user.id и context.application.bot_data["settings"]."""

from __future__ import annotations

from types import SimpleNamespace

from app.bot import access_control


def _settings(admin_tg_id):
    return SimpleNamespace(admin_tg_id=admin_tg_id)


def _update(user_id):
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(effective_user=user)


def _context(admin_tg_id):
    application = SimpleNamespace(bot_data={"settings": _settings(admin_tg_id)})
    return SimpleNamespace(application=application)


def test_admin_is_authorized():
    update = _update(42)
    context = _context(admin_tg_id=42)
    assert access_control.is_authorized(update, context) is True


def test_stranger_is_not_authorized():
    update = _update(999)
    context = _context(admin_tg_id=42)
    assert access_control.is_authorized(update, context) is False


def test_no_effective_user_is_not_authorized():
    update = _update(None)
    context = _context(admin_tg_id=42)
    assert access_control.is_authorized(update, context) is False


def test_unconfigured_admin_id_allows_everyone():
    """ADMIN_TG_ID не задан — не блокируем (бот ещё не настроен для
    приватности), но это отдельно логируется как предупреждение."""
    update = _update(999)
    context = _context(admin_tg_id=None)
    assert access_control.is_authorized(update, context) is True
