"""Ручное подтверждение перед запуском НАСТОЯЩЕГО агента Claude Code
(реальный доступ к файлам/bash в проекте, см.
app.providers.claude_code_cli.ClaudeCodeCliProvider.run_agentic_task) —
тот же принцип "✅ Разрешить", что уже есть для GITHUB_TOKEN (см.
app.providers.ai_autonomy, app.bot.job_runner.APPROVED_JOB_IDS), но не
для запуска job'ы целиком, а для одного вызова инструмента внутри
🗨 ИИ-чата (см. запрос пользователя: "выбор в начале будут ли вопросы
или ии сам будет выполнять" — governed by
app.providers.ai_autonomy.ai_command_auto_approve_enabled, тот же
тумблер, что уже решает то же самое для запуска задач).

In-memory, не БД — подтверждение живёт секунды-минуты, не переживает
рестарт бота и не обязано (если бот перезапустят посреди ожидания,
запрос просто истечёт по таймауту при следующей проверке)."""

from __future__ import annotations

import time
import uuid

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ALWAYS = "always"
DECISION_DEFER = "defer"

_PENDING: dict[str, str | None] = {}

POLL_SECONDS = 2
DEFAULT_TIMEOUT_SECONDS = 300


def create_pending() -> str:
    token = uuid.uuid4().hex[:12]
    _PENDING[token] = None
    return token


def resolve(token: str, decision: str) -> None:
    if token in _PENDING:
        _PENDING[token] = decision


def wait_for_decision(token: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str | None:
    waited = 0
    while _PENDING.get(token) is None:
        if waited >= timeout:
            _PENDING.pop(token, None)
            return None
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    return _PENDING.pop(token, None)
