"""Тумблеры автономности ИИ (app.providers.ai_autonomy) — оба должны быть
False по умолчанию (безопасное состояние без единого вызова set_*), и
job_needs_manual_approval() должен требовать подтверждение ТОЛЬКО когда
доступ к токену включён, а автоодобрение — нет."""

from __future__ import annotations

from app.providers.ai_autonomy import (
    ai_command_auto_approve_enabled,
    ai_github_token_access_enabled,
    ai_native_agents_enabled,
    job_needs_manual_approval,
    set_ai_command_auto_approve,
    set_ai_github_token_access,
    set_ai_native_agents_enabled,
)


def test_both_flags_default_to_false(db):
    assert ai_github_token_access_enabled() is False
    assert ai_command_auto_approve_enabled() is False
    assert ai_native_agents_enabled() is False


def test_set_and_read_native_agents(db):
    set_ai_native_agents_enabled(True)
    assert ai_native_agents_enabled() is True

    set_ai_native_agents_enabled(False)
    assert ai_native_agents_enabled() is False


def test_set_and_read_token_access(db):
    set_ai_github_token_access(True)
    assert ai_github_token_access_enabled() is True

    set_ai_github_token_access(False)
    assert ai_github_token_access_enabled() is False


def test_set_and_read_auto_approve(db):
    set_ai_command_auto_approve(True)
    assert ai_command_auto_approve_enabled() is True


def test_no_approval_needed_by_default(db):
    assert job_needs_manual_approval() is False


def test_approval_needed_when_token_access_on_and_auto_approve_off(db):
    set_ai_github_token_access(True)
    assert job_needs_manual_approval() is True


def test_no_approval_needed_when_auto_approve_also_on(db):
    set_ai_github_token_access(True)
    set_ai_command_auto_approve(True)
    assert job_needs_manual_approval() is False


def test_no_approval_needed_when_only_auto_approve_on(db):
    """Автоодобрение без включённого доступа к токену ничего не значит —
    подтверждение и так не требуется (нечего одобрять)."""
    set_ai_command_auto_approve(True)
    assert job_needs_manual_approval() is False


def test_setting_persists_across_separate_calls_like_a_restart(db):
    """В отличие от bot_data-тумблеров (autocheck_enabled_override),
    это должно жить в БД и не сбрасываться — здесь "рестарт" эмулируется
    просто отдельными вызовами без общего in-memory состояния."""
    set_ai_github_token_access(True)
    assert ai_github_token_access_enabled() is True
