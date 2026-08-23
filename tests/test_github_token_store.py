"""app.github_integration.token_store: токен из бота должен перекрывать
.env, а очистка override — возвращать поведение к .env-значению."""

from __future__ import annotations

from types import SimpleNamespace

from app.github_integration.token_store import (
    clear_token_override,
    get_token_override,
    resolve_github_token,
    set_token_override,
)


def _settings(github_token: str | None) -> SimpleNamespace:
    return SimpleNamespace(github_token=github_token)


def test_no_override_falls_back_to_env(db):
    assert get_token_override() is None
    assert resolve_github_token(_settings("ghp_from_env")) == "ghp_from_env"


def test_override_takes_priority_over_env(db):
    set_token_override("ghp_from_bot")
    assert resolve_github_token(_settings("ghp_from_env")) == "ghp_from_bot"


def test_override_persists_across_calls(db):
    set_token_override("ghp_first")
    set_token_override("ghp_second")
    assert get_token_override() == "ghp_second"


def test_clear_override_reverts_to_env(db):
    set_token_override("ghp_from_bot")
    clear_token_override()
    assert get_token_override() is None
    assert resolve_github_token(_settings("ghp_from_env")) == "ghp_from_env"


def test_clear_without_override_is_a_no_op(db):
    clear_token_override()
    assert get_token_override() is None


def test_resolve_with_neither_source_is_none(db):
    assert resolve_github_token(_settings(None)) is None
