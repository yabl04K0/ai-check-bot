"""app.ai_chat.approvals — ручное подтверждение перед запуском настоящего
агента (см. run_agentic_task), тот же принцип "✅ Разрешить", что уже есть
для GITHUB_TOKEN."""

from __future__ import annotations

import threading

from app.ai_chat.approvals import (
    DECISION_ALLOW,
    DECISION_ALWAYS,
    DECISION_DEFER,
    DECISION_DENY,
    create_pending,
    resolve,
    wait_for_decision,
)


def test_create_pending_returns_unique_tokens():
    a = create_pending()
    b = create_pending()
    assert a != b


def test_resolve_approved_unblocks_wait(monkeypatch):
    monkeypatch.setattr("app.ai_chat.approvals.POLL_SECONDS", 0)
    token = create_pending()

    def _approve_later():
        resolve(token, DECISION_ALLOW)

    timer = threading.Timer(0.05, _approve_later)
    timer.start()

    decision = wait_for_decision(token, timeout=5)
    timer.join()

    assert decision == DECISION_ALLOW


def test_resolve_rejected(monkeypatch):
    monkeypatch.setattr("app.ai_chat.approvals.POLL_SECONDS", 0)
    token = create_pending()
    resolve(token, DECISION_DENY)

    assert wait_for_decision(token, timeout=5) == DECISION_DENY


def test_resolve_always(monkeypatch):
    monkeypatch.setattr("app.ai_chat.approvals.POLL_SECONDS", 0)
    token = create_pending()
    resolve(token, DECISION_ALWAYS)

    assert wait_for_decision(token, timeout=5) == DECISION_ALWAYS


def test_resolve_defer(monkeypatch):
    monkeypatch.setattr("app.ai_chat.approvals.POLL_SECONDS", 0)
    token = create_pending()
    resolve(token, DECISION_DEFER)

    assert wait_for_decision(token, timeout=5) == DECISION_DEFER


def test_wait_for_decision_times_out(monkeypatch):
    monkeypatch.setattr("app.ai_chat.approvals.POLL_SECONDS", 0.01)
    token = create_pending()

    assert wait_for_decision(token, timeout=0) is None


def test_resolve_unknown_token_is_a_noop():
    resolve("does-not-exist", DECISION_ALLOW)
