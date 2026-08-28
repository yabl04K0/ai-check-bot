from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.handlers.settings_admin import (
    clear_custom_api_slot,
    cycle_custom_api_auth,
    cycle_custom_api_format,
    cycle_thinking_level,
    prompt_account_note,
    prompt_custom_api_field,
    receive_custom_api_text,
    send_accounts_list,
    show_agents,
    show_custom_api,
    show_custom_api_account,
    toggle_can_edit,
    toggle_can_push,
    toggle_show_limits,
)
from app.db.models import ProviderName
from app.providers.account_notes import get_note
from app.providers.agent_permissions import can_edit_code, can_push_github
from app.providers.ai_autonomy import ai_show_limits_to_model_enabled
from app.providers.claude_code_cli import ClaudeCodeCliProvider
from app.providers.custom_api import get_config
from app.providers.registry import ProviderRegistry
from app.providers.thinking import thinking_level


def _run(coro):
    return asyncio.run(coro)


def _update(data: str, admin_tg_id: int = 1, text: str | None = None):
    edit = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=edit, data=data)
    message = SimpleNamespace(text=text, reply_text=AsyncMock()) if text is not None else None
    return (
        SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=admin_tg_id),
            effective_chat=SimpleNamespace(id=admin_tg_id),
            message=message,
        ),
        query,
    )


def _context():
    registry = ProviderRegistry(
        {ProviderName.CLAUDE_CODE: ClaudeCodeCliProvider("claude", oauth_token="tok")}
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"provider_registry": registry}, bot=bot),
        bot=bot,
        user_data={},
    )


def _button_callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_show_agents_lists_thinking_and_limits(db):
    update, query = _update("set:agents")
    _run(show_agents(update, _context()))

    (text,), kwargs = query.edit_message_text.await_args
    assert "выключено" in text
    assert "ИИ видит свои лимиты" in text


def test_cycle_thinking_level_advances_and_wraps(db):
    update, query = _update("set:agents:thinking")
    ctx = _context()

    _run(cycle_thinking_level(update, ctx))
    assert thinking_level() == "low"
    _run(cycle_thinking_level(update, ctx))
    assert thinking_level() == "medium"
    _run(cycle_thinking_level(update, ctx))
    assert thinking_level() == "high"
    _run(cycle_thinking_level(update, ctx))
    assert thinking_level() == "off"


def test_toggle_show_limits_flips(db):
    update, query = _update("set:agents:toggle_limits")
    _run(toggle_show_limits(update, _context()))
    assert ai_show_limits_to_model_enabled() is True
    _run(toggle_show_limits(update, _context()))
    assert ai_show_limits_to_model_enabled() is False


def test_toggle_can_edit_flips_per_provider(db):
    update, query = _update("set:agents:edit:claude_code")
    assert can_edit_code(ProviderName.CLAUDE_CODE) is True
    _run(toggle_can_edit(update, _context()))
    assert can_edit_code(ProviderName.CLAUDE_CODE) is False


def test_toggle_can_push_flips_per_provider(db):
    update, query = _update("set:agents:push:claude_code")
    assert can_push_github(ProviderName.CLAUDE_CODE) is False
    _run(toggle_can_push(update, _context()))
    assert can_push_github(ProviderName.CLAUDE_CODE) is True


def test_send_accounts_list_reports_no_accounts_when_none_connected(db):
    update, query = _update("set:accounts_list")
    registry = ProviderRegistry({})
    ctx = SimpleNamespace(
        application=SimpleNamespace(bot_data={"provider_registry": registry}),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )
    _run(send_accounts_list(update, ctx))
    ctx.bot.send_message.assert_awaited_once()
    args, _ = ctx.bot.send_message.call_args
    assert "Нет ни одного" in args[1]


def test_send_accounts_list_sends_one_message_per_account_plus_header(db):
    update, query = _update("set:accounts_list")
    ctx = _context()
    _run(send_accounts_list(update, ctx))
    assert ctx.bot.send_message.await_count == 2


def test_show_custom_api_lists_primary_not_configured(db):
    update, query = _update("set:customapi")
    _run(show_custom_api(update, _context()))
    (text,), kwargs = query.edit_message_text.await_args
    assert "primary" in text
    assert "не настроен" in text


def test_prompt_custom_api_field_sets_awaiting(db):
    update, query = _update("set:customapi:name:primary")
    ctx = _context()
    _run(prompt_custom_api_field(update, ctx))
    assert ctx.user_data["awaiting"] == "customapi_name:primary"


def test_receive_custom_api_text_saves_name_then_url_then_model(db):
    ctx = _context()
    ctx.user_data["awaiting"] = "customapi_name:primary"
    update, _ = _update("noop", text="MyService")
    _run(receive_custom_api_text(update, ctx))
    assert get_config("primary").display_name == "MyService"
    assert ctx.user_data["awaiting"] is None

    ctx.user_data["awaiting"] = "customapi_url:primary"
    update, _ = _update("noop", text="https://api.example.com/v1")
    _run(receive_custom_api_text(update, ctx))
    assert get_config("primary").base_url == "https://api.example.com/v1"

    ctx.user_data["awaiting"] = "customapi_model:primary"
    update, _ = _update("noop", text="my-model")
    _run(receive_custom_api_text(update, ctx))

    config = get_config("primary")
    assert config.model == "my-model"
    assert config.is_configured is True


def test_receive_custom_api_text_auto_detects_name_from_url(db, monkeypatch):
    import app.bot.handlers.settings_admin as settings_admin_module

    monkeypatch.setattr(settings_admin_module, "detect_provider_name", lambda url: "Detected Name")
    ctx = _context()
    ctx.user_data["awaiting"] = "customapi_url:primary"
    update, _ = _update("noop", text="https://api.example.com/v1")
    _run(receive_custom_api_text(update, ctx))
    assert get_config("primary").display_name == "Detected Name"


def test_receive_custom_api_text_ignores_unrelated_awaiting(db):
    ctx = _context()
    ctx.user_data["awaiting"] = "comment"
    update, _ = _update("noop", text="whatever")
    _run(receive_custom_api_text(update, ctx))
    assert ctx.user_data["awaiting"] == "comment"


def test_cycle_custom_api_auth_and_format(db):
    update, query = _update("set:customapi:auth:primary")
    _run(cycle_custom_api_auth(update, _context()))
    assert get_config("primary").auth_style == "x-api-key"

    update, query = _update("set:customapi:format:primary")
    _run(cycle_custom_api_format(update, _context()))
    assert get_config("primary").response_format == "anthropic"


def test_cycle_custom_api_auth_handles_extra_label_with_colon(db):
    update, query = _update("set:customapi:auth:extra:1")
    _run(cycle_custom_api_auth(update, _context()))
    assert get_config("extra:1").auth_style == "x-api-key"
    assert get_config("primary").auth_style == "bearer"


def test_show_custom_api_account_renders_current_config(db):
    update, query = _update("set:customapi:primary")
    _run(show_custom_api_account(update, _context()))
    (text,), kwargs = query.edit_message_text.await_args
    assert "primary" in text


def test_show_custom_api_account_handles_extra_label_with_colon(db):
    update, query = _update("set:customapi:extra:1")
    _run(show_custom_api_account(update, _context()))
    (text,), kwargs = query.edit_message_text.await_args
    assert "extra:1" in text


def test_clear_custom_api_slot_resets_config(db):
    from app.providers.custom_api import set_config

    set_config("primary", display_name="X", base_url="https://x", model="m")
    update, query = _update("set:customapi:clear:primary")
    _run(clear_custom_api_slot(update, _context()))
    assert get_config("primary").is_configured is False


def test_prompt_account_note_sets_awaiting_with_colon_in_label(db):
    update, query = _update("set:accnote:claude_code:extra:1")
    ctx = _context()
    _run(prompt_account_note(update, ctx))
    assert ctx.user_data["awaiting"] == "accnote:claude_code:extra:1"


def test_receive_custom_api_text_saves_account_note(db):
    ctx = _context()
    ctx.user_data["awaiting"] = "accnote:claude_code:extra:1"
    update, _ = _update("noop", text="личный аккаунт")
    _run(receive_custom_api_text(update, ctx))
    assert get_note(ProviderName.CLAUDE_CODE, "extra:1") == "личный аккаунт"
    assert ctx.user_data["awaiting"] is None
