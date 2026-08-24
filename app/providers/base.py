"""Интерфейс AIProvider — единая точка входа для любого ИИ-провайдера.

Любой новый провайдер (Claude/Codex/Cursor/локальная LLM/будущие) обязан
реализовать этот интерфейс и НИЧЕГО больше — остальной код (пайплайны,
роутер, бот) работает только через него, никогда напрямую с SDK
конкретного провайдера.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.db.models import ProviderAccountStatus, ProviderName


class ProviderError(RuntimeError):
    """Ошибка вызова провайдера (сеть, авторизация, квота и т.д.)."""


class ProviderNotAuthenticatedError(ProviderError):
    """Провайдер не залогинен — нужно запустить login()."""


class ProviderQuotaExceededError(ProviderError):
    """Квота провайдера исчерпана — сигнал для HANDOVER-паттерна."""


@dataclass(frozen=True)
class ProviderResult:
    text: str
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    raw: object | None = None


@dataclass(frozen=True)
class QuotaEstimate:
    """Собственная оценка бота — официального API учёта квоты нет (см. README)."""

    used_pct: float | None
    hours_to_reset: float | None
    is_estimate: bool = True


@dataclass(frozen=True)
class AuthStatus:
    status: ProviderAccountStatus
    detail: str | None = None


@dataclass(frozen=True)
class LoginResult:
    """Результат попытки логина через сам бот (раздел 🔌 Провайдеры ИИ).

    success=True не всегда значит "уже подключен" — для CLI/OAuth-логина
    (Cursor/Codex) команда обычно печатает URL/код устройства и ждёт, пока
    человек авторизуется в браузере; в этом случае success=False, а message
    содержит инструкцию, и статус реально обновится только при следующей
    проверке auth_status()."""

    success: bool
    message: str


@dataclass
class RunOptions:
    model: str | None = None
    system: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.2
    extra: dict = field(default_factory=dict)


class AIProvider(ABC):
    """Базовый интерфейс. Каждый метод — то, что должен уметь провайдер,
    независимо от способа авторизации/вызова."""

    name: ProviderName

    @abstractmethod
    def auth_status(self) -> AuthStatus:
        """Подключен / не подключен / истёк — для раздела 🔌 Провайдеры ИИ."""

    @abstractmethod
    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        """Синхронный вызов провайдера с одним промптом. Поднимает ProviderError
        (или подклассы) при сбое — вызывающий код (пайплайн/роутер) решает,
        делать ли fallback на другой провайдер/тир."""

    def estimate_quota(self) -> QuotaEstimate:
        """По умолчанию — нет данных. Провайдеры с логируемым расходом
        (Claude/Codex) переопределяют на основе QuotaUsageLog."""
        return QuotaEstimate(used_pct=None, hours_to_reset=None)

    def supports_login(self) -> bool:
        """Показывать ли кнопку "Войти" в Настройках → 🔌 Провайдеры ИИ."""
        return False

    def login(self) -> LoginResult:
        """CLI/OAuth-логин, инициированный из бота (Cursor/Codex). По
        умолчанию не поддерживается — провайдеры на чистом API-ключе
        логинятся через .env, не через бота."""
        raise ProviderError(
            "Логин через бота не поддерживается для этого провайдера — "
            "настрой доступ через .env (см. .env.example)."
        )

    def supports_key_entry(self) -> bool:
        """Показывать ли кнопку "🔑 Ключ" в Настройках → 🔌 Провайдеры ИИ —
        для провайдеров на API-ключе (не CLI-логине вроде Cursor и не
        локалки без авторизации)."""
        return False

    def update_api_key(self, api_key: str | None) -> None:
        """Применить новый API-ключ к уже собранному инстансу провайдера —
        вызывается ботом сразу после сохранения (app.providers.key_store),
        без рестарта процесса. По умолчанию не поддерживается; провайдеры с
        supports_key_entry() == True обязаны переопределить это."""
        raise ProviderError(f"{self.name.value}: ввод ключа через бота не поддерживается.")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} name={self.name}>"
