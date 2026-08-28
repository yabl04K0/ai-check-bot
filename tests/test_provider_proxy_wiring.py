"""app.providers.openai_compatible._run_once передаёт назначенный прокси
в httpx.post(proxy=...) — та часть запроса пользователя "выдели [прокси]
под акки и апишки", которая реально применяется к вызову."""

from __future__ import annotations

import httpx

from app.db.models import ProviderName
from app.db.session import get_session
from app.providers.gemini import GeminiProvider
from app.proxies.pool import Consumer, assign_proxy


def _capture_proxy_kwarg(monkeypatch, *, usage_tokens: int = 1):
    """usage_tokens=0 — обходит QuotaTracker.record() (см. app/providers/quota.py,
    ранний return при input_tokens==output_tokens==0), чтобы изолированно
    проверять именно резолв прокси, не завися от отдельного требования
    quota-трекера к БД."""
    captured = {}

    def _fake_post(url, **kwargs):
        captured["proxy"] = kwargs.get("proxy")
        request = httpx.Request("POST", url)
        body = {
            "model": "test-model",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": usage_tokens, "completion_tokens": usage_tokens},
        }
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    return captured


def _add_proxy(session, host="1.2.3.4"):
    from app.db.models import ProxyPoolEntry, ProxyProtocol

    row = ProxyPoolEntry(host=host, port=1080, protocol=ProxyProtocol.SOCKS5, import_score=50.0)
    session.add(row)
    session.flush()
    return row


def test_run_prompt_uses_assigned_proxy(db, monkeypatch):
    with get_session() as session:
        _add_proxy(session)
        assign_proxy(session, Consumer(provider=ProviderName.GEMINI, account_label="primary"))

    captured = _capture_proxy_kwarg(monkeypatch)
    provider = GeminiProvider("api-key")

    provider.run_prompt("hi")

    assert captured["proxy"] == "socks5://1.2.3.4:1080"


def test_run_prompt_uses_no_proxy_when_unassigned(db, monkeypatch):
    captured = _capture_proxy_kwarg(monkeypatch)
    provider = GeminiProvider("api-key")

    provider.run_prompt("hi")

    assert captured["proxy"] is None


def test_run_prompt_does_not_crash_when_db_uninitialized(monkeypatch):
    """Регрессия на реальный баг, пойманный в этой же сессии: резолв
    прокси не должен требовать инициализированной БД, иначе падает любой
    вызов ИИ без неё. _SessionLocal форсируется в None напрямую — иначе
    более ранний тест в этом же процессе (через фикстуру db) мог бы
    незаметно оставить БД "инициализированной" и замаскировать баг."""
    import app.db.session as session_module

    monkeypatch.setattr(session_module, "_SessionLocal", None)
    captured = _capture_proxy_kwarg(monkeypatch, usage_tokens=0)
    provider = GeminiProvider("api-key")

    result = provider.run_prompt("hi")

    assert result.text == "ok"
    assert captured["proxy"] is None
