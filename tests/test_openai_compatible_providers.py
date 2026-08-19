"""Новые провайдеры (Gemini/DeepSeek/Grok/Groq/Mistral/OpenRouter/Together/
Perplexity/Fireworks/Cerebras) все реализованы через общий
app.providers.openai_compatible.OpenAICompatibleProvider — тестируем
контракт один раз через базовый класс плюс по разу на каждый подкласс
(правильные base_url/default_model/имя для сообщений об ошибках), чтобы
не дублировать одну и ту же HTTP-логику в 10 файлах тестов."""

from __future__ import annotations

import httpx
import pytest

from app.db.models import ProviderAccountStatus, ProviderName
from app.providers.base import ProviderError, ProviderNotAuthenticatedError, ProviderQuotaExceededError
from app.providers.cerebras import CerebrasProvider
from app.providers.deepseek import DeepSeekProvider
from app.providers.fireworks import FireworksProvider
from app.providers.gemini import GeminiProvider
from app.providers.grok import GrokProvider
from app.providers.groq import GroqProvider
from app.providers.mistral import MistralProvider
from app.providers.openrouter import OpenRouterProvider
from app.providers.perplexity import PerplexityProvider
from app.providers.together import TogetherProvider


def _fake_post_returning(status_code: int, json_body: dict | None = None):
    def _fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, json=json_body or {}, request=request)

    return _fake_post


def _fake_success(text: str = "ok"):
    def _fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        body = {
            "model": "test-model",
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
        return httpx.Response(200, json=body, request=request)

    return _fake_post


ALL_SUBCLASSES = [
    GeminiProvider,
    DeepSeekProvider,
    GrokProvider,
    GroqProvider,
    MistralProvider,
    OpenRouterProvider,
    TogetherProvider,
    PerplexityProvider,
    FireworksProvider,
    CerebrasProvider,
]


@pytest.mark.parametrize("provider_cls", ALL_SUBCLASSES)
def test_no_api_key_raises_not_authenticated(provider_cls):
    provider = provider_cls(None)
    with pytest.raises(ProviderNotAuthenticatedError):
        provider.run_prompt("привет")


@pytest.mark.parametrize("provider_cls", ALL_SUBCLASSES)
def test_no_api_key_reports_not_connected(provider_cls):
    provider = provider_cls(None)
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.NOT_CONNECTED


@pytest.mark.parametrize("provider_cls", ALL_SUBCLASSES)
def test_api_key_reports_connected(provider_cls):
    provider = provider_cls("test-key")
    status = provider.auth_status()
    assert status.status == ProviderAccountStatus.CONNECTED


@pytest.mark.parametrize("provider_cls", ALL_SUBCLASSES)
def test_429_raises_quota_exceeded(provider_cls, monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post_returning(429, {"error": "rate limited"}))
    provider = provider_cls("test-key")
    with pytest.raises(ProviderQuotaExceededError):
        provider.run_prompt("привет")


@pytest.mark.parametrize("provider_cls", ALL_SUBCLASSES)
def test_other_status_error_stays_generic(provider_cls, monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post_returning(500, {"error": "boom"}))
    provider = provider_cls("test-key")
    with pytest.raises(ProviderError) as exc_info:
        provider.run_prompt("привет")
    assert not isinstance(exc_info.value, ProviderQuotaExceededError)


@pytest.mark.parametrize("provider_cls", ALL_SUBCLASSES)
def test_successful_call_returns_text_and_records_usage(provider_cls, monkeypatch, db):
    monkeypatch.setattr(httpx, "post", _fake_success("привет от модели"))
    provider = provider_cls("test-key")

    result = provider.run_prompt("вопрос")

    assert result.text == "привет от модели"
    assert result.input_tokens == 5
    assert result.output_tokens == 7


def test_each_subclass_has_distinct_name_and_base_url():
    """Защита от copy-paste бага: два провайдера случайно не должны
    делить один ProviderName или один base_url."""
    instances = [cls("k") for cls in ALL_SUBCLASSES]
    names = [inst.name for inst in instances]
    base_urls = [inst._base_url for inst in instances]
    assert len(set(names)) == len(names)
    assert len(set(base_urls)) == len(base_urls)


def test_model_override_from_settings_is_respected():
    provider = GeminiProvider("k", model="gemini-custom-model")
    assert provider._model == "gemini-custom-model"


def test_default_model_used_when_no_override():
    from app.providers.gemini import DEFAULT_MODEL

    provider = GeminiProvider("k")
    assert provider._model == DEFAULT_MODEL


def test_all_new_providers_wired_into_registry():
    from pathlib import Path

    from app.config import AutocheckSettings, ProviderSettings, Settings
    from app.providers.registry import build_providers

    settings = Settings(
        bot_token=None,
        admin_tg_id=None,
        providers=ProviderSettings(
            gemini_api_key="g",
            deepseek_api_key="d",
            grok_api_key="x",
            groq_api_key="q",
            mistral_api_key="m",
            openrouter_api_key="o",
            together_api_key="t",
            perplexity_api_key="p",
            fireworks_api_key="f",
            cerebras_api_key="c",
        ),
        github_token=None,
        autocheck=AutocheckSettings(),
        db_path=Path("/tmp/unused.sqlite3"),
    )
    providers = build_providers(settings)

    for expected in (
        ProviderName.GEMINI,
        ProviderName.DEEPSEEK,
        ProviderName.GROK,
        ProviderName.GROQ,
        ProviderName.MISTRAL,
        ProviderName.OPENROUTER,
        ProviderName.TOGETHER,
        ProviderName.PERPLEXITY,
        ProviderName.FIREWORKS,
        ProviderName.CEREBRAS,
    ):
        assert expected in providers
        assert providers[expected].auth_status().status == ProviderAccountStatus.CONNECTED
