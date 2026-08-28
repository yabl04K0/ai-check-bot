"""Тумблеры автономности ИИ — сколько бот доверяет CLI-агентам (сейчас
Cursor, в будущем — non-interactive exec Codex CLI, см. TODO в codex.py)
действовать от имени GITHUB_TOKEN без пошагового участия человека.

Оба флага хранятся в BotSetting (переживают рестарт, в отличие от
bot_data-тумблеров вроде autocheck_enabled_override) и по умолчанию
ВЫКЛЮЧЕНЫ — небезопасное поведение только по явному opt-in через ⚙️
Настройки, с дисклеймером перед включением (см.
app/bot/handlers/settings_admin.py).

- AI_GITHUB_TOKEN_ACCESS: выключено — CLI-провайдер не получает
  GITHUB_TOKEN в окружении процесса вообще (см.
  app.providers.cursor.CursorProvider.run_prompt), т.е. даже если
  CLI-агент сам решит выполнить `git push`/`gh` изнутри (не то, о чём мы
  его просим в промпте — см. app.tasks.generic — но agentic CLI может
  сделать больше запрошенного), у него физически нет токена. Включено —
  токен передаётся, CLI-агент технически может сам управлять GitHub.
- AI_COMMAND_AUTO_APPROVE: выключено (по умолчанию) — пока включён
  AI_GITHUB_TOKEN_ACCESS, каждый запуск задачи требует отдельного
  явного тапа "✅ Разрешить" в чате перед стартом (см.
  app.bot.handlers.check.confirm), а не только общего подтверждения
  задачи. Включено — эта дополнительная проверка пропускается, задачи
  стартуют сразу же, как обычно.
"""

from __future__ import annotations

from app.db.models import BotSetting
from app.db.session import get_session

_KEY_GITHUB_TOKEN_ACCESS = "ai_github_token_access"
_KEY_COMMAND_AUTO_APPROVE = "ai_command_auto_approve"
_KEY_NATIVE_AGENTS = "ai_native_agents_enabled"
_KEY_SHOW_LIMITS_TO_MODEL = "ai_show_limits_to_model"


def _get_bool(key: str) -> bool:
    with get_session() as session:
        row = session.get(BotSetting, key)
        return row is not None and row.value == "true"


def _set_bool(key: str, enabled: bool) -> None:
    with get_session() as session:
        row = session.get(BotSetting, key)
        if row is None:
            session.add(BotSetting(key=key, value="true" if enabled else "false"))
        else:
            row.value = "true" if enabled else "false"


def ai_github_token_access_enabled() -> bool:
    return _get_bool(_KEY_GITHUB_TOKEN_ACCESS)


def set_ai_github_token_access(enabled: bool) -> None:
    _set_bool(_KEY_GITHUB_TOKEN_ACCESS, enabled)


def ai_command_auto_approve_enabled() -> bool:
    return _get_bool(_KEY_COMMAND_AUTO_APPROVE)


def set_ai_command_auto_approve(enabled: bool) -> None:
    _set_bool(_KEY_COMMAND_AUTO_APPROVE, enabled)


def ai_native_agents_enabled() -> bool:
    """Разрешён ли вообще запуск НАСТОЯЩИХ агентов Claude Code (реальный
    доступ к файлам/bash в проекте через --permission-mode bypassPermissions,
    см. app.providers.claude_code_cli.ClaudeCodeCliProvider.run_agentic_task)
    — принципиально другой уровень риска, чем остальные тумблеры этого
    модуля: там ИИ только СОВЕТУЕТ (текст промпта/патча), тут реально
    исполняет. Выключено по умолчанию, требует явного opt-in с
    дисклеймером (см. app.bot.handlers.settings_admin).

    Само по себе не решает, нужен ли тап "Разрешить" перед КАЖДЫМ
    запуском — за это отвечает ai_command_auto_approve_enabled() (тот же
    тумблер, что уже используется для одобрения запуска job'ы, см. запрос
    пользователя: "выбор в начале будут ли вопросы или ии сам будет
    выполнять" — включён auto-approve, агент стартует сразу; выключен —
    сначала уходит кнопка подтверждения, см. app.ai_chat.approvals)."""
    return _get_bool(_KEY_NATIVE_AGENTS)


def set_ai_native_agents_enabled(enabled: bool) -> None:
    _set_bool(_KEY_NATIVE_AGENTS, enabled)


def ai_show_limits_to_model_enabled() -> bool:
    return _get_bool(_KEY_SHOW_LIMITS_TO_MODEL)


def set_ai_show_limits_to_model(enabled: bool) -> None:
    _set_bool(_KEY_SHOW_LIMITS_TO_MODEL, enabled)


def job_needs_manual_approval() -> bool:
    """Задача перед стартом требует отдельного тапа "Разрешить", если ИИ
    сейчас в принципе может действовать с GITHUB_TOKEN, а автоодобрение
    не включено. Роутер выбирает провайдера уже во время самого запуска
    (см. app.bot.job_runner._run_pipeline_blocking), заранее неизвестно,
    достанется ли задача именно CLI-провайдеру — поэтому проверяем
    консервативно, по задаче в целом, а не пытаемся предсказать выбор
    роутера."""
    return ai_github_token_access_enabled() and not ai_command_auto_approve_enabled()
