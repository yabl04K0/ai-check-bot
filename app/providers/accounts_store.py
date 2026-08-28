"""Дополнительные аккаунты сверх основного слота — "➕ Добавить ещё аккаунт"
в ⚙️ Настройки → 🔌 Провайдеры ИИ → 🔑 Ключ: <provider>. Основной слот
(.env или "Задать/обновить" — см. app.providers.key_store) остаётся один,
как и раньше; тут — произвольное количество ДОПОЛНИТЕЛЬНЫХ секретов
(ProviderCredential), перебираемых по порядку при ошибке/квоте (см.
app.providers.multi_account.run_with_account_fallback)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import AccountTierAssignment, ProviderCredential, ProviderName, ProxyAssignment
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
    """account_label доп.аккаунтов позиционный ("extra:N", см.
    app.providers.multi_account.label_credentials) — AccountTierAssignment и
    ProxyAssignment ключуются этой же меткой. Удаление аккаунта, что не
    последний, сдвигает позиции всех следующих на один, поэтому их
    tier/proxy-назначения нужно явно перенести на новую метку — иначе они
    молча "уезжают" на другой физический аккаунт (см. chek_open.md)."""
    with get_session() as session:
        row = session.get(ProviderCredential, account_id)
        if row is None or row.provider != provider:
            return

        ordered_ids = session.scalars(
            select(ProviderCredential.id)
            .where(ProviderCredential.provider == provider)
            .order_by(ProviderCredential.id)
        ).all()
        removed_pos = ordered_ids.index(account_id) + 1
        total = len(ordered_ids)

        # Метка удаляемого аккаунта уходит вместе с ним — освобождаем её ДО
        # сдвига остальных и делаем flush(), иначе первый же сдвиг
        # (extra:{removed_pos+1} -> extra:{removed_pos}) столкнётся с
        # UniqueConstraint(provider, account_label), пока эта строка ещё
        # физически жива в БД.
        for model in (AccountTierAssignment, ProxyAssignment):
            stale = session.scalar(
                select(model).where(model.provider == provider, model.account_label == f"extra:{removed_pos}")
            )
            if stale is not None:
                session.delete(stale)
        session.flush()

        # По одному сдвигу за раз, с flush() после каждого: SQLAlchemy НЕ
        # гарантирует порядок UPDATE-выражений внутри одного flush() для
        # независимых строк одной таблицы, а следующий сдвиг требует, чтобы
        # метка-получатель предыдущего уже была физически свободна.
        for old_pos in range(removed_pos + 1, total + 1):
            old_label, new_label = f"extra:{old_pos}", f"extra:{old_pos - 1}"
            for model in (AccountTierAssignment, ProxyAssignment):
                assignment = session.scalar(
                    select(model).where(model.provider == provider, model.account_label == old_label)
                )
                if assignment is not None:
                    assignment.account_label = new_label
            session.flush()

        session.delete(row)
