from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.db.models import BotSetting, ProviderAccountStatus, ProviderName
from app.db.session import get_session
from app.providers.accounts_store import list_extra_accounts
from app.providers.base import (
    AIProvider,
    AuthStatus,
    ProviderError,
    ProviderNotAuthenticatedError,
    ProviderQuotaExceededError,
    ProviderResult,
    QuotaEstimate,
    RunOptions,
)
from app.providers.quota import QuotaTracker
from app.providers.rate_limit_headers import estimate_from_scraped
from app.providers.rate_limit_headers import scrape as scrape_rate_limit_headers
from app.proxies.pool import resolve_proxy_url_safe

AUTH_STYLES = ("bearer", "x-api-key", "none")
RESPONSE_FORMATS = ("openai", "anthropic")

_NAME_PREFIX = "custom_api_name"
_URL_PREFIX = "custom_api_base_url"
_MODEL_PREFIX = "custom_api_model"
_AUTH_PREFIX = "custom_api_auth_style"
_FORMAT_PREFIX = "custom_api_response_format"


@dataclass(frozen=True)
class CustomApiConfig:
    account_label: str
    display_name: str | None
    base_url: str | None
    model: str | None
    auth_style: str
    response_format: str

    @property
    def is_configured(self) -> bool:
        return bool(self.display_name and self.base_url and self.model)


def _get(key: str) -> str | None:
    with get_session() as session:
        row = session.get(BotSetting, key)
        return row.value if row and row.value else None


def _set(key: str, value: str) -> None:
    with get_session() as session:
        row = session.get(BotSetting, key)
        if row is None:
            session.add(BotSetting(key=key, value=value))
        else:
            row.value = value


def _clear(key: str) -> None:
    with get_session() as session:
        row = session.get(BotSetting, key)
        if row is not None:
            session.delete(row)


def get_config(account_label: str) -> CustomApiConfig:
    return CustomApiConfig(
        account_label=account_label,
        display_name=_get(f"{_NAME_PREFIX}:{account_label}"),
        base_url=_get(f"{_URL_PREFIX}:{account_label}"),
        model=_get(f"{_MODEL_PREFIX}:{account_label}"),
        auth_style=_get(f"{_AUTH_PREFIX}:{account_label}") or "bearer",
        response_format=_get(f"{_FORMAT_PREFIX}:{account_label}") or "openai",
    )


def known_account_labels() -> list[str]:
    return ["primary"] + [f"extra:{i}" for i in range(1, len(list_extra_accounts(ProviderName.CUSTOM)) + 1)]


def set_config(
    account_label: str,
    *,
    display_name: str,
    base_url: str,
    model: str,
    auth_style: str = "bearer",
    response_format: str = "openai",
) -> None:
    if auth_style not in AUTH_STYLES:
        raise ValueError(f"auth_style: {auth_style} (доступно: {', '.join(AUTH_STYLES)})")
    if response_format not in RESPONSE_FORMATS:
        raise ValueError(f"response_format: {response_format} (доступно: {', '.join(RESPONSE_FORMATS)})")
    _set(f"{_NAME_PREFIX}:{account_label}", display_name)
    _set(f"{_URL_PREFIX}:{account_label}", base_url.rstrip("/"))
    _set(f"{_MODEL_PREFIX}:{account_label}", model)
    _set(f"{_AUTH_PREFIX}:{account_label}", auth_style)
    _set(f"{_FORMAT_PREFIX}:{account_label}", response_format)


def set_auth_style(account_label: str, auth_style: str) -> None:
    if auth_style not in AUTH_STYLES:
        raise ValueError(f"auth_style: {auth_style} (доступно: {', '.join(AUTH_STYLES)})")
    _set(f"{_AUTH_PREFIX}:{account_label}", auth_style)


def set_response_format(account_label: str, response_format: str) -> None:
    if response_format not in RESPONSE_FORMATS:
        raise ValueError(f"response_format: {response_format} (доступно: {', '.join(RESPONSE_FORMATS)})")
    _set(f"{_FORMAT_PREFIX}:{account_label}", response_format)


def clear_config(account_label: str) -> None:
    _clear(f"{_NAME_PREFIX}:{account_label}")
    _clear(f"{_URL_PREFIX}:{account_label}")
    _clear(f"{_MODEL_PREFIX}:{account_label}")
    _clear(f"{_AUTH_PREFIX}:{account_label}")
    _clear(f"{_FORMAT_PREFIX}:{account_label}")


def _endpoint_url(base_url: str, response_format: str) -> str:
    if base_url.rstrip("/").endswith(("/chat/completions", "/messages")):
        return base_url
    return f"{base_url}/messages" if response_format == "anthropic" else f"{base_url}/chat/completions"


def _auth_headers(auth_style: str, api_key: str) -> dict[str, str]:
    if auth_style == "x-api-key":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if auth_style == "none":
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _name_from_host(base_url: str) -> str | None:
    host = httpx.URL(base_url).host
    if not host:
        return None
    parts = host.split(".")
    core = parts[-2] if len(parts) >= 2 else parts[0]
    return core.replace("-", " ").title() or None


def detect_provider_name(base_url: str, api_key: str | None = None, auth_style: str = "bearer") -> str | None:
    try:
        headers = _auth_headers(auth_style, api_key or "")
        response = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        entries = data.get("data") if isinstance(data, dict) else data
        if isinstance(entries, list) and entries:
            first = entries[0]
            if isinstance(first, dict):
                owner = first.get("owned_by") or first.get("owner")
                if owner and isinstance(owner, str):
                    return owner.replace("-", " ").replace("_", " ").title()
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
        pass
    return _name_from_host(base_url)


class CustomOpenAICompatibleProvider(AIProvider):
    name = ProviderName.CUSTOM

    def __init__(self, api_key: str | None, *, extra_accounts: list[str] | None = None) -> None:
        self._api_key = api_key
        self._extra_accounts = list(extra_accounts or [])
        self._last_rate_limit: dict[str, str] = {}

    def _credential_for(self, account_label: str) -> str | None:
        if account_label == "primary":
            return self._api_key
        if account_label.startswith("extra:"):
            index = int(account_label.split(":", 1)[1]) - 1
            if 0 <= index < len(self._extra_accounts):
                return self._extra_accounts[index]
        return None

    def set_extra_accounts(self, extra_accounts: list[str]) -> None:
        self._extra_accounts = list(extra_accounts)

    def supports_key_entry(self) -> bool:
        return True

    def update_api_key(self, api_key: str | None) -> None:
        self._api_key = api_key

    def auth_status(self) -> AuthStatus:
        labels = known_account_labels()
        configured = [label for label in labels if get_config(label).is_configured]
        if not configured:
            return AuthStatus(
                status=ProviderAccountStatus.NOT_CONNECTED,
                detail="ни один аккаунт не настроен — ⚙️ Настройки → 🤖 Настройки агентов → ➕ Свой API",
            )
        with_key = [
            label
            for label in configured
            if get_config(label).auth_style == "none" or self._credential_for(label)
        ]
        if not with_key:
            return AuthStatus(status=ProviderAccountStatus.NOT_CONNECTED, detail="ключ не задан")
        detail = (
            f"{len(with_key)} аккаунта(ов)" if len(with_key) > 1 else get_config(with_key[0]).display_name
        )
        return AuthStatus(status=ProviderAccountStatus.CONNECTED, detail=detail)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        options = options or RunOptions()
        account_label = options.forced_account_label or "primary"
        config = get_config(account_label)
        if not config.is_configured:
            raise ProviderNotAuthenticatedError(
                f"custom:{account_label}: слот не настроен (нет base_url/модели)."
            )
        credential = self._credential_for(account_label)
        if config.auth_style != "none" and not credential:
            raise ProviderNotAuthenticatedError(f"custom:{account_label}: ключ не задан.")
        return self._run_once(credential or "", prompt, options, config, account_label=account_label)

    def _run_once(
        self,
        api_key: str,
        prompt: str,
        options: RunOptions,
        config: CustomApiConfig,
        *,
        account_label: str,
    ) -> ProviderResult:
        model = options.model or config.model
        url = _endpoint_url(config.base_url, config.response_format)
        headers = _auth_headers(config.auth_style, api_key)
        proxy_url = resolve_proxy_url_safe(self.name, account_label)

        if config.response_format == "anthropic":
            body = {
                "model": model,
                "max_tokens": options.max_tokens,
                "temperature": options.temperature,
                "system": options.system or "",
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            messages = []
            if options.system:
                messages.append({"role": "system", "content": options.system})
            messages.append({"role": "user", "content": prompt})
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": options.max_tokens,
                "temperature": options.temperature,
            }

        try:
            response = httpx.post(url, headers=headers, json=body, timeout=180, proxy=proxy_url)
            self._last_rate_limit = scrape_rate_limit_headers(response.headers) or self._last_rate_limit
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._last_rate_limit = (
                scrape_rate_limit_headers(exc.response.headers) or self._last_rate_limit
            )
            if exc.response.status_code == 429:
                raise ProviderQuotaExceededError(
                    f"{config.display_name}: превышен лимит запросов (429): {exc}"
                ) from exc
            raise ProviderError(f"{config.display_name} API error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{config.display_name} network error: {exc}") from exc

        try:
            data = response.json()
            if config.response_format == "anthropic":
                blocks = data.get("content") or []
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                usage = data.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
            else:
                text = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{config.display_name}: не удалось разобрать ответ: {exc}") from exc

        QuotaTracker(self.name).record(
            model=data.get("model", model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            account_label=account_label,
        )
        return ProviderResult(
            text=text,
            model=data.get("model", model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=data,
        )

    def estimate_quota(self) -> QuotaEstimate:
        return estimate_from_scraped(self._last_rate_limit) or QuotaTracker(self.name).estimate()
