"""Cursor Agent CLI (`cursor-agent -p`) — логин в аккаунт Cursor.

Известные грабли (см. backend-architecture.mermaid): SOCKS5-прокси не
работает с cursor-agent — нужен HTTP-прокси; повторный запуск
editing-ролей не идемпотентен — вызывающий код (пайплайн) должен сам
следить, чтобы не звать одну и ту же editing-роль дважды на одном шаге.
"""

from __future__ import annotations

import subprocess

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import (
    AIProvider,
    AuthStatus,
    LoginResult,
    ProviderError,
    ProviderNotAuthenticatedError,
    ProviderQuotaExceededError,
    ProviderResult,
    RunOptions,
)
from app.providers.cli_login import run_cli_login

# cursor-agent — CLI-обёртка, у неё нет структурированного кода ошибки типа
# HTTP 429, только текст в stderr/stdout. Эвристика, а не гарантия: ищем
# явные признаки лимита в тексте ошибки, чтобы хотя бы для типичных формулировок
# сработал HANDOVER-паттерн вместо голого падения с ProviderError.
QUOTA_ERROR_MARKERS = ("rate limit", "rate-limit", "429", "quota", "too many requests")


def _looks_like_quota_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in QUOTA_ERROR_MARKERS)


class CursorProvider(AIProvider):
    name = ProviderName.CURSOR

    def __init__(self, cli_path: str | None) -> None:
        self._cli_path = cli_path

    def auth_status(self) -> AuthStatus:
        if not self._cli_path:
            return AuthStatus(
                status=ProviderAccountStatus.NOT_CONNECTED, detail="CURSOR_AGENT_CLI_PATH не задан"
            )
        try:
            result = subprocess.run(
                [self._cli_path, "status"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail=str(exc))
        if result.returncode == 0:
            return AuthStatus(status=ProviderAccountStatus.CONNECTED)
        return AuthStatus(
            status=ProviderAccountStatus.NOT_CONNECTED, detail=result.stderr.strip() or "не залогинен"
        )

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        if not self._cli_path:
            raise ProviderNotAuthenticatedError(
                "CURSOR_AGENT_CLI_PATH не задан — залогинься через `cursor-agent login`."
            )
        options = options or RunOptions()
        full_prompt = f"{options.system}\n\n{prompt}" if options.system else prompt
        try:
            result = subprocess.run(
                [self._cli_path, "-p", full_prompt],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProviderError(f"cursor-agent CLI error: {exc}") from exc

        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip()
            if _looks_like_quota_error(output):
                raise ProviderQuotaExceededError(f"cursor-agent: похоже на лимит/квоту: {output}")
            raise ProviderError(f"cursor-agent завершился с кодом {result.returncode}: {output}")

        return ProviderResult(text=result.stdout.strip(), model="cursor-agent", raw=result)

    def supports_login(self) -> bool:
        return bool(self._cli_path)

    def login(self) -> LoginResult:
        return run_cli_login(
            self._cli_path,
            missing_path_hint="CURSOR_AGENT_CLI_PATH не задан в .env — некуда запускать login.",
        )
