from __future__ import annotations

from app.db.models import ProviderName
from app.providers.agent_permissions import (
    can_edit_code,
    can_push_github,
    native_agent_always_allowed,
    set_can_edit_code,
    set_can_push_github,
    set_native_agent_always_allowed,
)


def test_can_edit_code_defaults_to_true_when_unset(db):
    assert can_edit_code(ProviderName.CLAUDE_CODE) is True


def test_can_push_github_defaults_to_false_when_unset(db):
    assert can_push_github(ProviderName.CLAUDE_CODE) is False


def test_set_can_edit_code_persists_and_is_read_back(db):
    set_can_edit_code(ProviderName.CLAUDE_CODE, False)
    assert can_edit_code(ProviderName.CLAUDE_CODE) is False

    set_can_edit_code(ProviderName.CLAUDE_CODE, True)
    assert can_edit_code(ProviderName.CLAUDE_CODE) is True


def test_set_can_push_github_persists_and_is_read_back(db):
    set_can_push_github(ProviderName.CLAUDE_CODE, True)
    assert can_push_github(ProviderName.CLAUDE_CODE) is True

    set_can_push_github(ProviderName.CLAUDE_CODE, False)
    assert can_push_github(ProviderName.CLAUDE_CODE) is False


def test_settings_are_independent_per_provider(db):
    set_can_edit_code(ProviderName.CLAUDE_CODE, False)
    set_can_push_github(ProviderName.CLAUDE_CODE, True)

    assert can_edit_code(ProviderName.CODEX) is True
    assert can_push_github(ProviderName.CODEX) is False


def test_edit_and_push_toggles_are_independent_keys(db):
    set_can_edit_code(ProviderName.CURSOR, False)

    assert can_edit_code(ProviderName.CURSOR) is False
    assert can_push_github(ProviderName.CURSOR) is False

    set_can_push_github(ProviderName.CURSOR, True)

    assert can_edit_code(ProviderName.CURSOR) is False
    assert can_push_github(ProviderName.CURSOR) is True


def test_set_can_edit_code_updates_existing_row_not_only_inserts(db):
    set_can_edit_code(ProviderName.GEMINI, False)
    set_can_edit_code(ProviderName.GEMINI, True)
    set_can_edit_code(ProviderName.GEMINI, False)
    assert can_edit_code(ProviderName.GEMINI) is False


def test_set_can_push_github_updates_existing_row_not_only_inserts(db):
    set_can_push_github(ProviderName.GEMINI, True)
    set_can_push_github(ProviderName.GEMINI, False)
    set_can_push_github(ProviderName.GEMINI, True)
    assert can_push_github(ProviderName.GEMINI) is True


def test_native_agent_always_allowed_defaults_to_false_when_unset(db):
    assert native_agent_always_allowed("demo") is False


def test_native_agent_always_allowed_persists_and_is_read_back(db):
    set_native_agent_always_allowed("demo", True)
    assert native_agent_always_allowed("demo") is True

    set_native_agent_always_allowed("demo", False)
    assert native_agent_always_allowed("demo") is False


def test_native_agent_always_allowed_is_scoped_per_project(db):
    set_native_agent_always_allowed("demo", True)

    assert native_agent_always_allowed("demo") is True
    assert native_agent_always_allowed("other") is False


def test_native_agent_always_allowed_updates_existing_row_not_only_inserts(db):
    set_native_agent_always_allowed("demo", True)
    set_native_agent_always_allowed("demo", False)
    set_native_agent_always_allowed("demo", True)
    assert native_agent_always_allowed("demo") is True
