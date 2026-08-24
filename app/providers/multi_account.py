"""Перебор нескольких аккаунтов одного провайдера — пробуем по порядку,
переключаемся на следующий при ЛЮБОЙ ProviderError (квота, протухший
ключ, что угодно — см. решение пользователя: "переключение только при
ошибке/квоте", не round-robin), поднимаем последнюю ошибку, если кончились
все. Общий для всех провайдеров с несколькими аккаунтами (см.
app.providers.openai_compatible/claude/codex/claude_code_cli)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from app.providers.base import ProviderError, ProviderNotAuthenticatedError, ProviderResult

T = TypeVar("T")


def run_with_account_fallback(
    credentials: Sequence[T], attempt: Callable[[T], ProviderResult], *, not_configured_hint: str
) -> ProviderResult:
    if not credentials:
        raise ProviderNotAuthenticatedError(not_configured_hint)

    last_error: ProviderError | None = None
    for credential in credentials:
        try:
            return attempt(credential)
        except ProviderError as exc:
            last_error = exc
    assert last_error is not None  # credentials non-empty ⇒ хотя бы одна попытка была
    raise last_error
