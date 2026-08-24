"""Дополнительные аккаунты сверх основного слота — "➕ Добавить ещё аккаунт"
в ⚙️ Настройки → 🔌 Провайдеры ИИ → 🔑 Ключ: <provider>. Основной слот
(.env или "Задать/обновить" — см. app.providers.key_store) остаётся один,
как и раньше; тут — произвольное количество ДОПОЛНИТЕЛЬНЫХ секретов
(ProviderCredential), перебираемых по порядку при ошибке/квоте (см.
app.providers.multi_account.run_with_account_fallback)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import ProviderCredential, ProviderName
from app.db.session import get_session


@dataclass(frozen=True)
class AccountEntry:
    id: int
    secret: str


def list_extra_accounts(provider: ProviderName) -> list[AccountEntry]:
    with get_session() as session:
        rows = session.scalars(
            select(ProviderCredential)
            .where(ProviderCredential.provider == provider)
            .order_by(ProviderCredential.id)
        ).all()
        return [AccountEntry(id=row.id, secret=row.secret) for row in rows]


def list_extra_secrets(provider: ProviderName) -> list[str]:
    """Только строки-секреты в порядке добавления — то, что нужно
    провайдеру для перебора (app.providers.registry.build_providers)."""
    return [entry.secret for entry in list_extra_accounts(provider)]


def add_extra_account(provider: ProviderName, secret: str) -> AccountEntry:
    with get_session() as session:
        row = ProviderCredential(provider=provider, secret=secret)
        session.add(row)
        session.flush()
        return AccountEntry(id=row.id, secret=row.secret)


def remove_extra_account(provider: ProviderName, account_id: int) -> None:
    with get_session() as session:
        row = session.get(ProviderCredential, account_id)
        if row is not None and row.provider == provider:
            session.delete(row)
