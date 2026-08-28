"""Перебор нескольких аккаунтов одного провайдера — пробуем по порядку,
переключаемся на следующий при ЛЮБОЙ ProviderError (квота, протухший
ключ, что угодно — см. решение пользователя: "переключение только при
ошибке/квоте", не round-robin). Если отвалились все — ошибка называет
КАЖДЫЙ аккаунт и что именно с ним не так, а не только последний: без
этого диагностика "какой из N аккаунтов реально битый" требует лезть в
шелл руками при каждом сбое (см. разбор живого 401 у 2 аккаунтов
claude_code — то же самое). Общий для всех провайдеров с несколькими
аккаунтами (см. app.providers.openai_compatible/claude/codex/claude_code_cli)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from app.providers.base import ProviderError, ProviderNotAuthenticatedError, ProviderResult

T = TypeVar("T")


def run_with_account_fallback(
    pairs: Sequence[tuple[str, T]],
    attempt: Callable[[str, T], ProviderResult],
    *,
    not_configured_hint: str,
    forced_account_label: str | None = None,
) -> ProviderResult:
    """forced_account_label (см. app.providers.base.RunOptions, приоритеты
    аккаунтов — app.providers.tiers) сужает перебор до ОДНОГО аккаунта:
    вызов должен уйти именно туда, а не первому живому по порядку. Если
    метка не найдена среди pairs — считаем это "не настроен", как и
    пустой pairs целиком, а не молча перебираем остальных (иначе тир
    "делегация" мог бы незаметно свалиться на аккаунт из тира "глава")."""
    if forced_account_label is not None:
        pairs = [(label, credential) for label, credential in pairs if label == forced_account_label]
    if not pairs:
        raise ProviderNotAuthenticatedError(not_configured_hint)

    errors: list[tuple[str, ProviderError]] = []
    for label, credential in pairs:
        try:
            return attempt(label, credential)
        except ProviderError as exc:
            errors.append((label, exc))

    # Тип последней ошибки решает, есть ли HANDOVER-пауза (см.
    # app.tasks.pipeline.Pipeline.run ловит именно ProviderQuotaExceededError)
    # — сохраняем его, но текст включает ВСЕ попытки, не только последнюю.
    summary = "; ".join(f"{label}: {exc}" for label, exc in errors)
    last_error = errors[-1][1]
    raise type(last_error)(f"все {len(errors)} аккаунт(ов) недоступны — {summary}") from last_error


def label_credentials(primary: str | None, extra_accounts: Sequence[str]) -> list[tuple[str, str]]:
    """(account_label, secret) в порядке перебора — "primary" первым (если
    задан), потом "extra:1", "extra:2"... по порядку добавления
    (app.providers.accounts_store). Метка идёт в QuotaUsageLog.account_label
    — чтобы "лимиты по аккаунтам" в UI знали, кто именно потратил токены."""
    pairs: list[tuple[str, str]] = []
    if primary:
        pairs.append(("primary", primary))
    pairs.extend((f"extra:{i}", secret) for i, secret in enumerate(extra_accounts, start=1))
    return pairs
