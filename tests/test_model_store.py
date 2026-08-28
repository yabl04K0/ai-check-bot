from __future__ import annotations

from app.config import ProviderSettings
from app.db.models import ProviderName
from app.providers.model_store import (
    clear_model_override,
    env_default_model,
    get_model_override,
    resolve_model,
    set_model_override,
    supports_model_override,
)


def test_supports_model_override_true_for_openai_compatible_provider():
    assert supports_model_override(ProviderName.GROQ) is True


def test_supports_model_override_false_for_cli_provider():
    assert supports_model_override(ProviderName.CLAUDE_CODE) is False


def test_get_model_override_none_when_unset(db):
    assert get_model_override(ProviderName.GROQ) is None


def test_set_then_get_model_override(db):
    set_model_override(ProviderName.GROQ, "llama-3.1-8b-instant")
    assert get_model_override(ProviderName.GROQ) == "llama-3.1-8b-instant"


def test_set_model_override_updates_existing(db):
    set_model_override(ProviderName.GROQ, "model-a")
    set_model_override(ProviderName.GROQ, "model-b")
    assert get_model_override(ProviderName.GROQ) == "model-b"


def test_clear_model_override(db):
    set_model_override(ProviderName.GROQ, "model-a")
    clear_model_override(ProviderName.GROQ)
    assert get_model_override(ProviderName.GROQ) is None


def test_env_default_model_reads_settings_field():
    providers = ProviderSettings(groq_model="env-model")
    assert env_default_model(ProviderName.GROQ, providers) == "env-model"


def test_resolve_model_prefers_bot_override_over_env(db):
    set_model_override(ProviderName.GROQ, "bot-model")
    providers = ProviderSettings(groq_model="env-model")
    assert resolve_model(ProviderName.GROQ, providers) == "bot-model"


def test_resolve_model_falls_back_to_env_when_no_override(db):
    providers = ProviderSettings(groq_model="env-model")
    assert resolve_model(ProviderName.GROQ, providers) == "env-model"


def test_resolve_model_none_when_neither_set(db):
    providers = ProviderSettings()
    assert resolve_model(ProviderName.GROQ, providers) is None
