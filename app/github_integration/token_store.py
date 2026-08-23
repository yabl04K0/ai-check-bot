"""GitHub-токен, который можно задать/обновить прямо из бота (⚙️ Настройки
→ 🐙 GitHub → 🔑 Токен), в дополнение к статичному GITHUB_TOKEN в .env.

Хранится в BotSetting — том же key/value сторе, что и тумблеры автономности
ИИ (см. app.providers.ai_autonomy) — переживает рестарт бота.

Приоритет: токен, заданный через бота (если есть), перекрывает .env, а не
наоборот — через бота токен можно сменить без правки файла и рестарта
процесса; чтобы вернуться к значению из .env, нужно явно нажать «Убрать» в
меню токена."""

from __future__ import annotations

from app.config import Settings
from app.db.models import BotSetting
from app.db.session import get_session

_KEY = "github_token_override"


def get_token_override() -> str | None:
    with get_session() as session:
        row = session.get(BotSetting, _KEY)
        return row.value if row and row.value else None


def set_token_override(token: str) -> None:
    with get_session() as session:
        row = session.get(BotSetting, _KEY)
        if row is None:
            session.add(BotSetting(key=_KEY, value=token))
        else:
            row.value = token


def clear_token_override() -> None:
    with get_session() as session:
        row = session.get(BotSetting, _KEY)
        if row is not None:
            session.delete(row)


def resolve_github_token(settings: Settings) -> str | None:
    """Токен из бота (если задан) имеет приоритет над .env — см. докстринг модуля."""
    return get_token_override() or settings.github_token
