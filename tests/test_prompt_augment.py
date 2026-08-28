from __future__ import annotations

from app.db.models import ProviderName
from app.providers.ai_autonomy import set_ai_show_limits_to_model
from app.providers.base import ProviderResult, RunOptions
from app.providers.prompt_augment import PromptAugmentProvider, build_augmented_options
from app.providers.quota import QuotaTracker
from app.providers.thinking import set_thinking_level, thinking_instruction


class _FakeProvider:
    def __init__(self, name: ProviderName, text: str = "ok"):
        self.name = name
        self._text = text
        self.calls: list[RunOptions | None] = []
        self.custom_flag = "from-inner"

    def auth_status(self):
        raise AssertionError("not needed for these tests")

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        self.calls.append(options)
        return ProviderResult(text=self._text)

    def some_extra_method(self):
        return "inner-method-result"


def _record_usage(provider, account_label, input_tokens, output_tokens):
    QuotaTracker(provider).record(
        model="m", input_tokens=input_tokens, output_tokens=output_tokens, account_label=account_label
    )


def test_build_augmented_options_returns_same_object_when_nothing_to_add(db):
    options = RunOptions(system="base system")
    result = build_augmented_options(ProviderName.CLAUDE_CODE, options)
    assert result is options


def test_build_augmented_options_creates_default_options_when_none_passed(db):
    result = build_augmented_options(ProviderName.CLAUDE_CODE, None)
    assert result.system is None
    assert result.max_tokens == 4096
    assert result.temperature == 0.2


def test_build_augmented_options_adds_thinking_instruction_when_level_set(db):
    set_thinking_level("medium")
    result = build_augmented_options(ProviderName.CLAUDE_CODE, None)
    assert result.system == thinking_instruction("medium")


def test_build_augmented_options_appends_thinking_instruction_after_existing_system(db):
    set_thinking_level("high")
    options = RunOptions(system="Existing system prompt")
    result = build_augmented_options(ProviderName.CLAUDE_CODE, options)
    assert result.system == f"Existing system prompt\n\n{thinking_instruction('high')}"


def test_build_augmented_options_off_level_and_no_limits_leaves_options_untouched(db):
    set_thinking_level("off")
    options = RunOptions(system="unchanged")
    result = build_augmented_options(ProviderName.CLAUDE_CODE, options, force_limits=False)
    assert result is options


def test_build_augmented_options_force_limits_adds_usage_note_even_when_toggle_disabled(db):
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 100, 50)
    result = build_augmented_options(ProviderName.CLAUDE_CODE, None, force_limits=True)
    assert "Твой текущий расход квоты" in result.system
    assert "claude_code" in result.system


def test_build_augmented_options_toggle_enabled_adds_usage_note_without_force_limits(db):
    set_ai_show_limits_to_model(True)
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 100, 50)
    result = build_augmented_options(ProviderName.CLAUDE_CODE, None, force_limits=False)
    assert "Твой текущий расход квоты" in result.system


def test_build_augmented_options_toggle_disabled_and_no_force_limits_skips_usage_note(db):
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 100, 50)
    result = build_augmented_options(ProviderName.CLAUDE_CODE, RunOptions(), force_limits=False)
    assert result == RunOptions()


def test_build_augmented_options_force_limits_with_no_usage_data_adds_nothing(db):
    result = build_augmented_options(ProviderName.CLAUDE_CODE, RunOptions(), force_limits=True)
    assert result == RunOptions()


def test_build_augmented_options_usage_note_exact_format(db):
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 100, 50)
    result = build_augmented_options(ProviderName.CLAUDE_CODE, None, force_limits=True)
    assert result.system == "Твой текущий расход квоты (claude_code): primary: 5ч 150/нед 150"


def test_build_augmented_options_usage_note_sorts_accounts_by_label(db):
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 10, 0)
    _record_usage(ProviderName.CLAUDE_CODE, "extra:1", 20, 0)
    result = build_augmented_options(ProviderName.CLAUDE_CODE, None, force_limits=True)
    extra_pos = result.system.index("extra:1")
    primary_pos = result.system.index("primary")
    assert extra_pos < primary_pos


def test_build_augmented_options_combines_thinking_and_limits_in_order(db):
    set_thinking_level("low")
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 10, 0)
    result = build_augmented_options(ProviderName.CLAUDE_CODE, None, force_limits=True)
    thinking_pos = result.system.index(thinking_instruction("low"))
    limits_pos = result.system.index("Твой текущий расход квоты")
    assert thinking_pos < limits_pos
    assert "\n" in result.system


def test_build_augmented_options_preserves_other_run_options_fields(db):
    set_thinking_level("low")
    options = RunOptions(
        model="custom-model",
        max_tokens=999,
        temperature=0.7,
        extra={"foo": "bar"},
        forced_account_label="extra:1",
    )
    result = build_augmented_options(ProviderName.CLAUDE_CODE, options)
    assert result.model == "custom-model"
    assert result.max_tokens == 999
    assert result.temperature == 0.7
    assert result.extra == {"foo": "bar"}
    assert result.forced_account_label == "extra:1"


def test_prompt_augment_provider_name_matches_inner_name(db):
    inner = _FakeProvider(ProviderName.CLAUDE_CODE)
    wrapper = PromptAugmentProvider(inner)
    assert wrapper.name == ProviderName.CLAUDE_CODE


def test_prompt_augment_provider_getattr_passthrough(db):
    inner = _FakeProvider(ProviderName.CLAUDE_CODE)
    wrapper = PromptAugmentProvider(inner)
    assert wrapper.custom_flag == "from-inner"
    assert wrapper.some_extra_method() == "inner-method-result"


def test_prompt_augment_provider_forwards_augmented_options_to_inner(db):
    set_thinking_level("low")
    inner = _FakeProvider(ProviderName.CLAUDE_CODE, text="hello")
    wrapper = PromptAugmentProvider(inner)

    result = wrapper.run_prompt("hi")

    assert result.text == "hello"
    assert len(inner.calls) == 1
    assert inner.calls[0].system == thinking_instruction("low")


def test_prompt_augment_provider_run_prompt_without_options_does_not_crash(db):
    inner = _FakeProvider(ProviderName.CLAUDE_CODE)
    wrapper = PromptAugmentProvider(inner)
    result = wrapper.run_prompt("hi")
    assert result.text == "ok"
    assert inner.calls[0] is not None


def test_prompt_augment_provider_force_limits_constructor_flag_adds_usage_note(db):
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 10, 0)
    inner = _FakeProvider(ProviderName.CLAUDE_CODE)
    wrapper = PromptAugmentProvider(inner, force_limits=True)

    wrapper.run_prompt("hi")

    assert "Твой текущий расход квоты" in inner.calls[0].system


def test_prompt_augment_provider_without_force_limits_does_not_add_usage_note(db):
    _record_usage(ProviderName.CLAUDE_CODE, "primary", 10, 0)
    inner = _FakeProvider(ProviderName.CLAUDE_CODE)
    wrapper = PromptAugmentProvider(inner, force_limits=False)

    wrapper.run_prompt("hi")

    assert inner.calls[0].system is None
