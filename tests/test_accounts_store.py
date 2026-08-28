"""remove_extra_account перенумерует позиционные "extra:N"-метки оставшихся
доп.аккаунтов, чтобы AccountTierAssignment/ProxyAssignment (ключуются той же
меткой, см. app.providers.multi_account.label_credentials) не "уезжали"
молча на другой физический аккаунт при удалении НЕ последнего аккаунта."""

from __future__ import annotations

from app.db.models import AccountPriority, ProviderName, ProxyPoolEntry, ProxyProtocol
from app.db.session import get_session
from app.providers.accounts_store import add_extra_account, list_extra_accounts, remove_extra_account
from app.providers.tiers import get_tier, set_tier
from app.proxies.pool import Consumer, assign_proxy, get_assignment


def _add_proxy(session, host: str, *, score: float = 50.0) -> ProxyPoolEntry:
    row = ProxyPoolEntry(host=host, port=1080, protocol=ProxyProtocol.SOCKS5, import_score=score)
    session.add(row)
    session.flush()
    return row


def test_remove_last_extra_account_no_shift(db):
    a1 = add_extra_account(ProviderName.GROQ, "secret-1")
    a2 = add_extra_account(ProviderName.GROQ, "secret-2")
    a3 = add_extra_account(ProviderName.GROQ, "secret-3")
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.HEAD)
    set_tier(ProviderName.GROQ, "extra:2", AccountPriority.DELEGATION)

    remove_extra_account(ProviderName.GROQ, a3.id)

    remaining = list_extra_accounts(ProviderName.GROQ)
    assert [entry.id for entry in remaining] == [a1.id, a2.id]
    assert get_tier(ProviderName.GROQ, "extra:1") == AccountPriority.HEAD
    assert get_tier(ProviderName.GROQ, "extra:2") == AccountPriority.DELEGATION


def test_remove_middle_extra_account_shifts_tier_and_proxy(db):
    add_extra_account(ProviderName.GROQ, "secret-1")
    add_extra_account(ProviderName.GROQ, "secret-2")
    a3 = add_extra_account(ProviderName.GROQ, "secret-3")
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.HEAD)
    set_tier(ProviderName.GROQ, "extra:3", AccountPriority.DELEGATION)
    with get_session() as session:
        _add_proxy(session, "9.9.9.9")
        assign_proxy(session, Consumer(provider=ProviderName.GROQ, account_label="extra:3"))

    # extra:2 — не последний, удаляем именно его.
    to_remove = list_extra_accounts(ProviderName.GROQ)[1]
    assert to_remove.secret == "secret-2"
    remove_extra_account(ProviderName.GROQ, to_remove.id)

    remaining = list_extra_accounts(ProviderName.GROQ)
    assert [entry.id for entry in remaining] == [remaining[0].id, a3.id]
    assert remaining[1].id == a3.id  # физический аккаунт a3 теперь на позиции extra:2

    assert get_tier(ProviderName.GROQ, "extra:1") == AccountPriority.HEAD  # не тронут
    assert get_tier(ProviderName.GROQ, "extra:2") == AccountPriority.DELEGATION  # сдвинуто с extra:3
    assert get_tier(ProviderName.GROQ, "extra:3") is None  # больше никого на этой позиции

    with get_session() as session:
        moved = get_assignment(session, Consumer(provider=ProviderName.GROQ, account_label="extra:2"))
        assert moved is not None
        assert moved.proxy.host == "9.9.9.9"
        assert get_assignment(session, Consumer(provider=ProviderName.GROQ, account_label="extra:3")) is None


def test_remove_account_without_any_assignment_does_not_crash_or_leak(db):
    a1 = add_extra_account(ProviderName.GROQ, "secret-1")
    add_extra_account(ProviderName.GROQ, "secret-2")  # без тира/прокси вовсе

    remove_extra_account(ProviderName.GROQ, a1.id)

    remaining = list_extra_accounts(ProviderName.GROQ)
    assert len(remaining) == 1
    assert get_tier(ProviderName.GROQ, "extra:1") is None
    assert get_tier(ProviderName.GROQ, "extra:2") is None


def test_remove_wrong_provider_or_unknown_id_is_noop(db):
    a1 = add_extra_account(ProviderName.GROQ, "secret-1")
    set_tier(ProviderName.GROQ, "extra:1", AccountPriority.HEAD)

    remove_extra_account(ProviderName.DEEPSEEK, a1.id)  # чужой провайдер
    remove_extra_account(ProviderName.GROQ, account_id=999999)  # несуществующий id

    remaining = list_extra_accounts(ProviderName.GROQ)
    assert [entry.id for entry in remaining] == [a1.id]
    assert get_tier(ProviderName.GROQ, "extra:1") == AccountPriority.HEAD
