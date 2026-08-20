from types import SimpleNamespace

from ai_check_bot.input_flow import pop_waiting, set_waiting


def _ctx():
    return SimpleNamespace(user_data={})


def test_set_then_pop_returns_entry():
    ctx = _ctx()
    set_waiting(ctx, "add_account")
    entry = pop_waiting(ctx)
    assert entry == {"kind": "add_account"}


def test_pop_clears_state():
    ctx = _ctx()
    set_waiting(ctx, "add_account")
    pop_waiting(ctx)
    assert ctx.user_data == {}
    assert pop_waiting(ctx) is None


def test_pop_without_set_returns_none():
    ctx = _ctx()
    assert pop_waiting(ctx) is None


def test_extra_kwargs_roundtrip():
    ctx = _ctx()
    set_waiting(ctx, "set_proxy", account_id="7")
    entry = pop_waiting(ctx)
    assert entry == {"kind": "set_proxy", "account_id": "7"}


def test_expired_entry_returns_none():
    ctx = _ctx()
    set_waiting(ctx, "add_account")
    ctx.user_data["waiting_for_set_at"] -= 1000  # simulate it being set long ago
    assert pop_waiting(ctx, ttl_seconds=1) is None
    assert ctx.user_data == {}  # still cleared, not left stuck
