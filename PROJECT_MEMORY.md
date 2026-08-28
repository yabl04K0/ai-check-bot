# PROJECT_MEMORY — ai-check-bot durable architecture memory + session log

FORMAT: dense, flat, LLM-oriented (not prose for humans). Sections above SESSION LOG
are durable architecture/invariants, read at the start of every session. SESSION LOG
is newest-first (new entries go right after this header, oldest entries sink to the
bottom) — opposite order from STATE_LOG.md, which is append-at-end/oldest-first.

## What this project is

Telegram bot (python-telegram-bot v21+) that runs the "ЧЕК" AI-collaboration protocol
(the same md-file continuity system used manually in sibling projects like MeCelium —
CHEK_PROTOCOL.md/AI_COMMANDS.md/chek_open|later|never.md/LAST_PROMPT.md/STATE_LOG.md)
through a Telegram UI instead of a human typing commands to a CLI AI session. Personal
single-owner tool, not a product — see README for the full feature surface.

## Core architecture invariants

- DB (SQLite, `data/bot.sqlite3`) is a CACHE, never the source of truth. Source of
  truth for findings/continuity is each target project's own `chek_open.md` /
  `chek_later.md` / `chek_never.md` / `LAST_PROMPT.md` / `STATE_LOG.md` /
  `PROJECT_MEMORY.md` files (see `app/registry_store/*.py`). DB `Finding` rows are
  synced from those files after every commit (`sync_project_findings`), never the
  other way around.
- No Alembic/migrations — `app/db/session.py::_MISSING_COLUMNS` does manual
  `ALTER TABLE` for new columns on existing DBs; `create_all()` handles brand-new
  tables. Every schema change touches this dict.
- `AIProvider` is the ONLY interface pipelines/router/bot ever touch — never a
  provider SDK directly outside `app/providers/*.py`. 13 providers: `claude` (API),
  `claude_code_cli` (`claude -p`, subscription-based), `codex`, `cursor`, `local_llm`,
  plus 9 sharing `OpenAICompatibleProvider` (gemini/deepseek/grok/groq/mistral/
  openrouter/together/perplexity/fireworks/cerebras).
- Multi-account per provider: `label_credentials()` gives `"primary"` + `"extra:N"`
  labels IN POSITIONAL ORDER (`ProviderCredential.id` ascending) — this positional
  scheme is a KNOWN FRAGILITY, see chek_open.md `accounts_store.remove_extra_account`.
- Account priority tiers (`app/providers/tiers.py`): HEAD/MEDIUM/DELEGATION per
  (provider, account_label), global on/off toggle (BotSetting, default OFF).
  `run_prompt_with_tier()` used by pipeline steps: silent fallback to `ctx.provider`
  if nothing applies — NEVER hard-fails a job over incomplete tier config. `TierPicker`
  round-robins a tier's accounts across parallel fleet-checker domains so N domains
  get N different accounts.
- Per-job tier override (`JobAccountTierAssignment`, added 2026-08-27 — see user
  request: "при включении задачи... список с иишками которые будут работать с
  проектом и приоритет на этом этапе"): the job-creation wizard
  (`app/bot/handlers/check.py::_ai_picker_view`, screen between comment and confirm)
  lets the user assign HEAD/MEDIUM/DELEGATION to specific accounts for THIS job only,
  written via `tiers.py::set_job_tier` right after `queue.enqueue()`. Precedence,
  entirely inside `run_prompt_with_tier`/`TierPicker.__init__`: if the job has ANY
  `JobAccountTierAssignment` row (`job_has_tier_overrides`), tier routing for that job
  uses ONLY those rows — an account with a GLOBAL tier but no per-job priority does
  NOT participate in that job, by design (user's own rule: "если не поставил приоритет
  на какую то ии то значит не используем её в задаче"). Zero rows for the job (wizard
  skipped, or nothing tapped) means full fallback to the global `AccountTierAssignment`
  table, unchanged from before this feature ("если не задал ни одного приоритета то
  значит используем настройки из настроек"). A job override also BYPASSES the global
  `delegation_mode_enabled` toggle — picking AIs for one task is itself an explicit
  intent, independent of whether the global switch happens to be off. `TIER_ICON`/
  `TIER_RU_NAME`/`TIER_CYCLE` (cycle: not-set→HEAD→MEDIUM→DELEGATION→not-set) moved
  from being private to `settings_admin.py` into `tiers.py` as public constants so the
  wizard screen and the ⚙️ Настройки → 🎚 Приоритеты аккаунтов screen render identically
  — single source, not two copies that could drift.
- `ctx.state` keys prefixed `_` (currently only `run_prompt_with_tier`'s
  `"_tier_picker"`, a live `TierPicker` instance) are RUNTIME-ONLY and never persisted:
  `Pipeline.run` (see HANDOVER/resume writeup below) strips them before
  `json.dumps(ctx.state)`. Found and fixed 2026-08-27 while extending this same tier
  system: without the strip, a resumed job's `default=str` fallback would have turned
  the live picker into a garbage string, and the next `run_prompt_with_tier` call would
  crash calling `.pick()` on a `str`. Any FUTURE non-JSON-safe key added to `ctx.state`
  must use this `_`-prefix convention or it will hit the same bug on resume.
- Proxy pool (`app/proxies/*.py`, added this session): per-(provider,account_label)
  proxy assignment, sourced from MeCelium's DB (read-only) or manual `ss://`/`host:port`
  paste. Shadowsocks needs a local Xray SOCKS5 bridge (`xray_bridge.py`) since httpx
  can't dial `ss://` directly — reuses the v2rayN-installed `xray.exe` binary.
- 🗨 Group AI-chat (`app/ai_chat/*.py` + `app/bot/handlers/ai_chat.py`, added this
  session): free-form multi-account chat, ONE shared `AiChatMessage` history per
  `AiChatSession`. Tool-calling protocol is plain text (`ДЕЙСТВИЕ: name | args`), not
  native function-calling — works uniformly across all 13 providers, same principle
  as `findings_parse.parse_structured_findings`. Two "agent" mechanisms, deliberately
  different risk levels:
  - `delegate` tool: ask another tier's account a text question, always available,
    no filesystem/bash access — just another conversational participant.
  - `run_native_agent` tool: a REAL Claude Code CLI agent (`--permission-mode
    bypassPermissions`, `cwd=` the target project) with actual file/bash access on
    the user's own subscription. Double-gated: `ai_native_agents_enabled` (global,
    default OFF, disclaimer) + per-invocation Telegram approval unless
    `ai_command_auto_approve_enabled` is on (same toggle that already gates job-start
    approval — reused, not a new mode enum).
  - Live progress: `AiChatSession.status_detail` written by `run_turn` at each step,
    polled every 2s by `app/bot/handlers/ai_chat.py::_poll_status` to live-edit a
    status message — otherwise the whole multi-step tool-calling loop (up to
    `MAX_TOOL_STEPS`) was invisible behind a single static "typing…" indicator.
- Job pipeline progress (`app/bot/job_runner.py::_progress_loop` +
  `app/bot/formatting.py::render_progress`) already had a real live progress bar
  (▓░ blocks + %) before this session — the "add live visual feedback" gap this
  session was specifically the chat, not the job pipeline.
- UI conventions (`app/bot/keyboards.py`): `nav_row()` for any screen deeper than
  1 level (gives both ◀️ Назад AND 🏠 Меню), `confirm_row()` before any irreversible
  action, `paginate_rows()` at 8/page for any list that can grow unbounded.
  `context.user_data["awaiting"]` is one-shot everywhere EXCEPT `"ai_chat"` (stays
  set across messages until explicit close) — every screen that can be reached via
  ◀️ Назад from an awaiting-prompt must reset `awaiting`, not just 🏠 Меню.

## Cross-provider quota fallback (`app/providers/chain_fallback.py`, added 2026-08-27)

User request: "подготовь проект к работе без остановок... шпарит до момента пока не
закончатся все лимиты, ток тогда он встанет и будет ждать пока один из акков не
обновится." Before this: `pick_provider()` picked ONE provider for the whole job at
start; `router.fallback_chain()` existed but was dead code, never called; on
`ProviderQuotaExceededError` (raised only after `multi_account.run_with_account_fallback`
already exhausted that ONE provider's own accounts) the WHOLE job paused via HANDOVER
even if other connected providers had quota. Fix: `ChainFallbackProvider` wraps
`ctx.provider` (job_runner.py, INSIDE `NoteTrackingProvider` so logging/progress_detail
always reflects whoever actually answered — `.name` is a plain mutable attribute updated
on switch, read fresh on each `NoteTrackingProvider` log line, not cached). On
`ProviderError` it tries every OTHER provider in `fallback_chain(job.task_type)` (not
just those after the failed one — ALL of them, since a resumed job might restart from
wherever it paused, not from chain position 0) that's enabled+connected, in priority
order; only raises (type of the LAST attempted error, mirroring
`run_with_account_fallback`'s own precedent) once every candidate has failed. Not
applied to `forced_account_label` calls (tier-routing) — those need one specific
account; `run_prompt_with_tier` instead catches `ProviderError` around its own
tier-assigned call and falls through to `ctx.provider` (now chain-fallback-wrapped) on
failure. `_resume_tick` (autocheck.py) no longer checks only `job.provider`'s own quota
estimate — `_chain_has_available_provider` checks the WHOLE chain, since after a mid-run
switch `job.provider` at pause time could be ANY member of the chain, not necessarily
the one that will recover first.
Real subtlety found+fixed while building this: `ChainFallbackProvider._persist_switch`
writes `job.provider` via its OWN short `get_session()` (same reason as
`NoteTrackingProvider` — `ctx.session` isn't thread-safe, parallel fleet-checker threads
share it). `_run_pipeline_blocking`'s own long-lived `ctx.session` does NOT see that
write automatically (SQLAlchemy doesn't refresh one session's already-loaded object from
another session's commit) — without an explicit `session.refresh(job,
attribute_names=["provider"])` right after `pipeline.run()` returns, `HistoryEntry.provider`
would have recorded the ORIGINAL provider even when the job actually finished on a
different one after a mid-run switch. Regression test
(`tests/test_job_runner.py::test_mid_run_provider_switch_is_visible_in_history_entry`)
was verified to actually fail without the refresh line before being kept (reverted it,
ran red, restored it, ran green) — not just written and trusted.
Tests: `tests/test_chain_fallback.py` (7, new file), `tests/test_tiers.py` (+1),
`tests/test_autocheck_tick.py` (+5), `tests/test_job_runner.py` (1, new file — this
project's first test to exercise `_run_pipeline_blocking` directly instead of mocking it
out). 675 passed total, same 4 pre-existing unrelated cursor-agent Windows failures.

## Known fragile spots

chek_open.md is empty as of 2026-08-26 — both bugs tracked there (pipeline resume,
account_label renumbering) are fixed, see the two writeups below and the SESSION LOG.

## accounts_store.remove_extra_account renumbering (fixed 2026-08-26)

`account_label` for extra accounts is positional ("extra:N", derived at read-time from
`ProviderCredential.id` ascending order, never stored — see `multi_account.label_credentials`).
`AccountTierAssignment`/`ProxyAssignment` key on that same string. Deleting a NON-last extra
account used to leave everyone above it mislabeled — the account that used to be extra:3
silently becomes extra:2 on the next read, inheriting whatever tier/proxy was assigned to
the OLD extra:2 (now deleted), while the new extra:3 slot got nothing. Fix, inside
`remove_extra_account`, same `with get_session()` transaction as the delete: (1) delete
`AccountTierAssignment`/`ProxyAssignment` rows at the REMOVED position's label first (they
belonged to the account being deleted, would otherwise collide with the shift below) and
`session.flush()`; (2) shift every higher position's assignment rows down by one label,
ONE POSITION AT A TIME with a `session.flush()` after each — SQLAlchemy does not guarantee
UPDATE statement ordering within a single flush for unrelated rows of the same table, and
this rename is exactly the classic "swap two unique-key values" hazard: without a flush
between steps, a later UPDATE can race an earlier one and hit
`UniqueConstraint(provider, account_label)` before the label it's moving into is actually
vacated in the DB. Regression tests: `tests/test_accounts_store.py` (new file) — covers
last-position delete (no shift), middle-position delete (tier + proxy both correctly follow
the surviving physical account, not the label), an account with no assignment at all, and
the pre-existing wrong-provider/unknown-id no-op behavior.

## HANDOVER/resume persistence (`app/tasks/pipeline.py::Pipeline.run`, fixed 2026-08-26)

Every `start_job()` call builds a brand-new `StepContext` (`ctx.state = {}`) even when
resuming a `PAUSED_QUOTA` job (see `app/bot/job_runner.py::_run_pipeline_blocking`) — a
fresh in-memory dict, not shared across process runs. `Job.state_json` (new column) is
the fix: `Pipeline.run` now `json.dumps(ctx.state, default=str)` + commits it after every
successfully completed step (alongside the existing `progress_step` update), and on
entry, if `job.progress_step > 0` and `state_json` is set, restores it into `ctx.state`
via `json.loads` before the step loop, then skips `step.run()` for every step whose index
is `<= progress_step`. A corrupt/unparseable `state_json` falls back to re-running from
step 1 (`already_done = 0`) rather than continuing with a partial/broken state. All step
outputs across all three pipelines (protocol_full/protocol_lite/generic) are plain
JSON-safe types (str/list[str]/dict[str,str]/bool/int/None) — verified by reading every
Step.run() in protocol_full.py/protocol_lite.py/generic.py before writing this fix, no
serialization surprises expected. Regression test:
`tests/test_pipeline.py::test_pipeline_resume_skips_done_steps_and_restores_state`.

## Job control (2026-08-28)

`app/tasks/queue.py::add_live_note` appends a timestamped line to `Job.live_notes`;
`Pipeline.run` refreshes `ctx.job.live_notes` (`session.refresh`, cross-session
staleness — same class of bug as the ChainFallbackProvider one below) and rebuilds
`ctx.comment` from it before EVERY step, so a comment added mid-run via the 💬 button
in `progress_menu` reaches the next step's prompt without restarting the job.
Archive (📦 button, same menu): reuses `CANCEL_REQUESTS` to stop the pipeline cleanly,
then `app/bot/job_runner.py::_send_handoff_document` builds a markdown dump
(`app/tasks/archive_export.py::build_handoff_markdown` — comment/live_notes/
progress_detail/report_text/patch_text/handover_note) and sends it as a Telegram
document, so the user can paste it into a DIFFERENT AI tool to keep working there.

## AI-asks-a-question (2026-08-28)

`app/tasks/clarify.py` — text-marker protocol like `ai_chat`'s `ДЕЙСТВИЕ:` but for a
free-text question: a Step's system prompt teaches the model `ВОПРОС: <text>`; the
step detects it, calls `ctx.ask_user(question)` (new `StepContext` method), which sets
`Job.pending_question` + `JobStatus.PAUSED_QUESTION`, sends the question to Telegram,
and blocks the worker thread polling an in-memory dict until a NEW handler
(`app/bot/handlers/clarify.py`, `MessageHandler` group=8) captures the user's next
plain-text reply for that job. Wired into ONLY `GenericStep1Plan` (feature/fix/
refactor/custom) this session — protocol_full/lite steps don't use it yet, the
mechanism (`ctx.ask_user`) is generic and reusable there later. Native Claude Code CLI
agent runs (`run_agentic_task`, `--permission-mode bypassPermissions`, non-interactive
`-p`) CANNOT be paused mid-run to ask a question — no TTY, hard technical constraint,
not an oversight — `run_native_agent` stays a single fire-and-forget call.

## Cross-provider/account resilience (2026-08-28)

Three layers, all in `app/providers/`:
- `circuit_breaker.py` — in-memory `(provider, account_label) -> last-failure-time`,
  5-min cooldown (`COOLDOWN_SECONDS`); `is_open()` skips a just-failed candidate
  instead of re-trying it on literally every subsequent call. Wired into
  `ChainFallbackProvider` (keyed by a `"_chain"` sentinel label — that consumer only
  knows provider names, not accounts) and into `tiers.py`'s `run_prompt_with_tier`/
  `call_tier_account` (keyed by the real `account_label`). Module state MUST be reset
  between tests — `tests/conftest.py` has an autouse `_reset_circuit_breaker` fixture;
  forgetting this on a new test file will NOT crash, it'll just silently skip accounts
  that an EARLIER test in the same run marked failed.
- `TierPicker.pick_all(priority)` (was just `pick()`, single account) returns the WHOLE
  tier as a round-robin-rotated list. `run_prompt_with_tier`/`call_tier_account` now
  loop over ALL of it (skipping disabled/circuit-open ones) before falling back to
  `ctx.provider`/`None` — this is what makes "put 3 accounts all on tier 👑 Глава"
  actually behave like one resilient neural network (user's words: "лимит главной
  иишки это реально проблема, если он закончится всё встанет") instead of trying
  exactly one tier account and giving up.
- `app/scheduler/quota_warnings.py::check_and_warn` — new APScheduler tick (same
  `TICK_INTERVAL_MINUTES` cadence as `autocheck._tick`), warns `admin_tg_id` ONCE
  (dedup via a module-level `_WARNED` set) when any 👑 Глава account crosses 85% used,
  clears the dedup flag when usage drops back below so a future re-crossing warns again.

## Real quota data (2026-08-28)

Most quota data in this bot is `QuotaTracker`'s own token-counting estimate against a
`.env` weekly budget (no official API exists for most of these — see README). This
session added a SECOND, more trustworthy source where the wire protocol actually
provides one: `app/providers/rate_limit_headers.py` (pure functions, shared) scrapes
`x-ratelimit-*`/`anthropic-ratelimit-*`/`retry-after` response headers into a
`QuotaEstimate(is_estimate=False)` — wired into `OpenAICompatibleProvider` (covers all
10 subclasses: gemini/deepseek/grok/groq/mistral/openrouter/together/perplexity/
fireworks/cerebras) and `CustomOpenAICompatibleProvider` (below). `estimate_quota()`
on both prefers real header data over the DB self-estimate when present.
`app/providers/quota.py::account_quota_estimate_for(registry, provider, account_label)`
is the one function UI code should call for "how much quota does THIS account have
left" — it already does the real-vs-estimate preference + per-account DB scoping
(`account_quota_estimate`, filtered by `account_label`, unlike the older
provider-wide `QuotaTracker.estimate()`). NOT real for `claude_code_cli`/`cursor`/
`codex` — CLI wrappers, no HTTP headers the bot's own client ever sees; the Anthropic
NATIVE API (`ClaudeProvider`) also still uses the self-estimate only — its SDK call
wasn't switched to raw-response mode this session, a real remaining gap if precise
Claude-API quota ever matters more than it does now.

## Custom API provider slots (2026-08-28, revised same day)

First cut was 3 FIXED `ProviderName.CUSTOM_1/2/3` slots. User pushed back twice
("я же просил неограниченное кол-во слотов") — redesigned to genuinely unlimited
WITHOUT the big `ProviderName`-enum-to-string-ID refactor originally assumed
necessary: single `ProviderName.CUSTOM` enum member, unlimited ACCOUNTS under it via
the SAME "primary"/"extra:N" positional-label convention every other multi-account
provider in this codebase already uses (`app.providers.accounts_store` — secrets via
`ProviderCredential`, unlimited additions, existing renumber-on-delete logic reused
for free). Each account_label gets its OWN metadata in `app/providers/custom_api.py`
(BotSetting-backed, keyed by label not by provider): display name, base_url, model,
auth style (`bearer`/`x-api-key`/`none`), response format (`openai` chat-completions
shape or `anthropic` Messages shape) — `known_account_labels()` lists them,
`⚙️ Настройки → 🤖 Настройки агентов → ➕ Свой API → 🔑 Ключи` reuses the EXISTING
generic key-management screen (`set:key:custom`) for adding/removing the secrets
themselves, this feature's own screens are metadata-only.

Deliberate behavior change from the old design: `CustomOpenAICompatibleProvider.run_prompt`
does NOT auto-fallback across its own accounts the way every other multi-account
provider does — different custom accounts are typically DIFFERENT arbitrary services
(not interchangeable fallbacks of the same one), so trying "primary" then blindly
trying "extra:1" on failure would silently route a prompt to the wrong service. With
`options.forced_account_label` set (tier/job-picker routing already does this) it uses
exactly that account; unrouted calls use `"primary"` only, erroring clearly rather than
guessing if that's not configured.

New: `detect_provider_name(base_url, ...)` — best-effort auto-suggest for the display
name when the user sets a base_url (tries `GET {base_url}/models`, reads `owned_by`
from the first entry; falls back to deriving a name from the URL's hostname; the
"✏️ Имя" field stays manually editable either way — user's explicit ask: "я должен сам
вводить имя провайдера к кастомному апи если не вышло взять название провайдера из апи").

This closed the `ProviderName`-enum concern entirely for THIS feature — no schema/
enum refactor was actually needed, the multi-account infrastructure already solved
"unlimited accounts of one provider" and custom APIs just needed to become accounts
of one CUSTOM provider instead of separate provider identities. The bigger
string-ID-provider refactor the user chose earlier as "the eventual right answer" is
now understood to be a DIFFERENT, narrower problem than originally framed (it would
only still matter for something that needs N independent PROVIDER IDENTITIES with
their own tier/quota/circuit-breaker semantics, not for "N accounts of arbitrary
services" — which this design already gives for free) — deprioritize it unless a
concrete need for true multi-provider-identity dynamism shows up.

## Agent permissions (2026-08-28)

`app/providers/agent_permissions.py` — per-PROVIDER refinement on top of the existing
GLOBAL `ai_autonomy` toggles (those decide "at all", this decides "which provider"):
`can_edit_code` (default True — CLI agent write access, maps to
`claude_code_cli.run_agentic_task`'s `--permission-mode bypassPermissions` vs `plan`)
and `can_push_github` (default: explicit per-provider override if one was ever set,
ELSE True only for whichever provider currently has an account on tier 👑 Глава via
`accounts_in_tier(HEAD)`, ELSE False — "не главным нейронкам нельзя работать с
гитхабом, главная сама пушит когда надо", user's words). Closed a REAL pre-existing
gap while wiring this: `claude_code_cli.py` never gated `GITHUB_TOKEN` on ANYTHING
before this session (unlike `cursor.py`, which always did) — a native agent could
already `git push`/`gh` using whatever ambient git credentials the machine had,
regardless of any bot toggle. Now gated the same way Cursor is.

## Other new pieces (2026-08-28)

- `app/providers/thinking.py` + `app/providers/prompt_augment.py::PromptAugmentProvider`
  — global "уровень мышления" (off/low/medium/high) appended to every `RunOptions.system`
  as a plain-text instruction (works uniformly through every provider, same trick as the
  `ДЕЙСТВИЕ:`/`ВОПРОС:` markers); also injects a quota-usage note when
  `ai_show_limits_to_model_enabled()` is on OR the call is on behalf of a 👑 Глава/
  orchestrator role (`force_limits=True`, passed explicitly at each of the ~4 call sites
  that construct one). Wrap ChainFallbackProvider OUTSIDE PromptAugmentProvider (not the
  other way) — `RunOptions` is a plain dataclass forwarded by value, so augmenting it
  BEFORE the chain sees it means every fallback candidate gets the augmentation too.
- `app/providers/account_notes.py` — free-text comment per (provider, account_label),
  BotSetting-backed, shown in the new "📋 Список аккаунтов" screen
  (`settings_admin.py::send_accounts_list` — sends ONE Telegram message per account,
  each with its own status/quota/circuit-state/comment and a "💬 Изменить коммент"
  button, per the user's explicit ask for that shape).
- `app/tasks/web_research.py` + two new `app/ai_chat/tools.py` entries (`web_search`,
  `fetch_url`) — no API key needed: `web_search` scrapes DuckDuckGo's keyless HTML
  endpoint (`html.duckduckgo.com/html/`, regex-parsed — fragile if DDG changes markup,
  no official API contract), `fetch_url` strips HTML via stdlib `html.parser` (no new
  dependency). Both cache in-process for 10 min (`_cache` dict) to avoid repeat fetches
  burning latency within one chat session. Same `full_access`-gated allowlist as every
  other `ai_chat` tool — see that module's docstring for why (no raw fs/process access).
- `app/ai_chat/tools.py::send_file` — path-traversal-safe (resolves against the
  project's `local_path`, rejects anything whose resolved path isn't inside it —
  covers both `../` traversal AND a full absolute-path escape attempt, both tested).

## AI-chat auto-compaction (2026-08-28)

`app/ai_chat/compact.py::maybe_compact` — the `/compact` analogue the user asked for
early in this session and that got lost in the shuffle until the user pushed back at
the very end ("ты закончил разве с проектом?"). Called once at the top of every
`run_turn`, before the new user message is persisted. Gate: session has more than
`KEEP_RECENT_MESSAGES` (12) messages AND their combined `len(content)` exceeds
`COMPACT_THRESHOLD_CHARS` (600_000 — a deliberately conservative real-world default,
not literally "1M tokens" as the user's own mental model put it, since most connected
providers cap out well under that in practice; picking a number close to the ceiling
would mean compaction kicks in only AFTER a provider call has already started failing).
Summarizes everything except the last 12 messages via one provider call (same
orchestrator-selection logic as the main turn, `_pick_orchestrator`, wrapped in
`PromptAugmentProvider`), then — since `AiChatMessage.id` is an autoincrement PK with
no way to control ordering on insert — REUSES the first compacted message's row (role/
author/content mutated in place, `author="система: сжатие контекста"` to stay visually
distinct) and deletes the rest of the compacted range, instead of inserting a new row
(which would sort AFTER the kept recent messages — wrong chronological order). Sends
one Telegram notification with real before/after counts. A `ProviderError` during
summarization leaves history completely untouched and just skips compaction for this
turn (logged, not raised) — better to skip than risk a corrupted history.

## Live activity overview (2026-08-28)

Closed the other early-session gap that got lost in the shuffle: "писал скок агентов
запущено, какой у них прогресс" — the 🤖 Активность screen (`menu:activity` in
`app/bot/keyboards.py::main_menu`, handler in `app/bot/handlers/menu.py::activity_text`/
`show_activity`) shows three sections on one static render-on-open screen (no
auto-refresh poll loop, unlike the per-job progress message — deliberately out of
scope, re-tap to refresh): jobs in flight (`Job.status` in RUNNING/PAUSED_MANUAL/
PAUSED_QUESTION/PAUSED_QUOTA, reusing `render_job_status_line`), running native
Claude Code CLI agents (`app/ai_chat/agent_activity.py` — new in-memory
`start`/`finish`/`active()` registry, thread-safe since `run_turn` executes via
`asyncio.to_thread` and multiple chat turns could in principle overlap; wired as pure
instrumentation around `provider.run_agentic_task(...)` in
`_tool_run_native_agent`, `finish()` always in a `finally`), and 🗨 chats with a live
turn in progress (`app/ai_chat/sessions.py::sessions_with_live_status` — open sessions
with a non-null `status_detail`). No live sub-step detail inside one agent run (e.g.
"editing file X right now") — `run_agentic_task` is a blocking subprocess call with no
intermediate event stream to hook into; would need deeper CLI integration, tracked in
RESEARCH_BACKLOG.md instead of attempted here.

## Custom API — see the redesign note (2026-08-28, revised) further up this file

Not repeating it here — search this file for "Custom API provider slots" for the full
writeup of the 3-fixed-slots → unlimited-accounts-under-one-provider redesign that
happened mid-session after user pushback, including why the originally-assumed
`ProviderName`-enum refactor turned out to be unnecessary for this specific ask.

## Research backlog (2026-08-28)

`RESEARCH_BACKLOG.md` (new file, repo root) — three parallel research passes (Telegram/
personal-AI-dev-bot UX, multi-agent audit-pipeline academic + industry patterns,
automated-code-review-SaaS landscape) surfaced ~25 concrete, cited, adoptable ideas
beyond what got implemented this session. Deliberately NOT implemented now — this
session was already enormous and cramming more in risks regressions faster than they
can be verified. One trivial win WAS applied immediately: critic-root-cause.md (in the
shared `0000` kit repo) now explicitly checks for "compensatory" fixes (routes around a
problem via an unrelated mechanism) as a third category alongside root-cause-vs-
symptom, from SIADAFIX (arXiv:2510.16059). Read RESEARCH_BACKLOG.md before starting a
session focused on protocol-quality/UX improvements rather than new bot features —
it's pre-triaged by priority and flags what's cheap-safe vs. what needs real design
work (e.g. budget-aware fleet sizing) vs. what was deliberately rejected (e.g.
default-proceed-on-timeout for the file/bash-access approval gate specifically, since
that gate's default-deny is a real safety property, not just UX friction, unlike the
Devin "plan approval" context the pattern was borrowed from).

## Three RESEARCH_BACKLOG items promoted to done (2026-08-28, same-day continuation)

User pushed once more after the closing report ("да я это имел ввиду [чек фулл на себе] но
ты уже прошёлся по проекту, продолжай делать проект и нереализованные мои слова, применяй
строчки и подходы из других проектов") — took that as "implement some of the backlog now,
not just document it." Picked the 3 cheapest/safest/best-scoped items, ran them as 3
parallel Sonnet agents (see RESEARCH_BACKLOG.md's "Реализовано" section for the terse
version, this is the fuller writeup):

- **4-way native-agent approval** (Allow/Deny/Always/Defer) — `app/ai_chat/approvals.py`'s
  `resolve`/`wait_for_decision` changed from bool to a 4-value string contract
  (`DECISION_ALLOW/DENY/ALWAYS/DEFER`, timeout still `None`); `_await_agent_approval` in
  `app/ai_chat/tools.py` renders 4 buttons; Always persists a per-PROJECT (not global —
  scoping it project-wide would be too broad a grant) standing flag via new
  `app/providers/agent_permissions.py::native_agent_always_allowed`/
  `set_native_agent_always_allowed`, checked alongside the existing global
  `ai_command_auto_approve_enabled()` so either one skips the prompt; Defer resolves
  immediately with a message distinct from both deny and timeout. Deliberately did NOT
  adopt the OTHER half of the same research finding (default-proceed-on-timeout) — see the
  rejection note in RESEARCH_BACKLOG.md's low-priority section, still applies.
- **Nightly per-project check independent of quota** — `Project.nightly_check_time`
  (`"HH:MM"`, local time, `None`=off) + `Project.nightly_last_run_date` (dedup-per-day)
  new columns; `app/scheduler/autocheck.py::_nightly_tick` (5-min cadence, reuses the
  existing `_PENDING_AUTO_STATUSES` no-double-enqueue guard from `_tick`) fires
  `TaskType.CHECK_FULL` through the same `JobQueue.enqueue`/`start_job` path every other
  auto-trigger uses. UI lives in the existing per-project detail screen in
  `app/bot/handlers/projects.py` (`proj:nightly:{id}` prompts "ЧЧ:ММ",
  `proj:nightly_clear:{id}` disables) — NOT a new top-level menu, this is project-scoped
  config like `autocheck_enabled` already is.
- **Chat context threaded into `run_native_agent`** — `ToolContext` gained
  `session_id: int | None = None` (default chosen specifically so no other
  `ToolContext(...)` construction site anywhere in the repo needed touching);
  `orchestrator.py::run_turn` is the only real constructor, now passes its own
  `session_id` through. `_tool_run_native_agent` prepends the last 8 `AiChatMessage`
  rows (role-prefixed, same shape as `orchestrator._history_prompt`) to the `task` string
  before it reaches `run_agentic_task` — `run_agentic_task`'s own signature is untouched,
  this is pure prompt construction on the caller side. No session / no history -> exact
  old behavior, verified by a dedicated test (this matters: a context block that's present
  but silently empty would have been a subtler bug than an outright crash).

All 3 ran as genuinely parallel Sonnet agents (one touched `app/ai_chat/tools.py`
concurrently with a 4th unrelated file the visual-polish pass was touching — no collision,
verified after the fact) — each one independently ran ruff + full suite before reporting,
and I re-verified all three myself again after they landed rather than taking the reports
on faith (same discipline as every other delegation this session). Also did a light,
deliberately-scoped visual pass across the highest-traffic PRE-existing screens (main menu
text in `app/bot/handlers/start.py`, `_settings_view`/`_agents_view` headers and
`limits_text` in `app/bot/handlers/menu.py`) — a consistent `┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄` divider +
title-case header convention, staying plain-text/no-parse_mode (see the earlier
"markdown-escape crashes" audit finding elsewhere in this file for why parse_mode is
avoided) — NOT a full redesign of every screen (registry/github list screens deliberately
left alone, already appropriately dense-as-information rather than needing decoration).

## Critic decorrelation + registry fuzzy dedup + pre-fleet plan approval + confidence badge + budget-aware fleet + critic exchange + discard-changes button + escalation crux + health monitor + real Claude Code % (2026-08-28, later same day — STOPPED HERE, see note at end)

Two small RESEARCH_BACKLOG.md items done directly (no agent dispatch — user asked to economize
limits after a weekly rate-limit hit mid-session), both reusing existing infrastructure rather
than adding anything new:
- **Different tiers for the two Full ЧЕК critics** — decorrelation research (arXiv:2502.08788)
  says model/provider diversity matters more than prompt diversity alone. `app/tasks/
  protocol_full.py`: Critic-A always `AccountPriority.HEAD`, Critic-B always `AccountPriority.
  MEDIUM`, via one shared `_run_critic(ctx, focus, tier, fix)` helper used by both `Step10Critics`
  (first pass) and `Step11ConvergenceLoop` (up to 3 retry rounds) — previously each had its own
  inline closure hardcoding `HEAD` for both critics.
- **Fuzzy fallback dedup for later/never findings** — `app/registry_store/store.py::
  _is_reworded_duplicate`. Catches the case where a checker reformulates an already-dismissed
  finding's `file_symbol`/description on a later run and exact-string matching misses it,
  silently reopening something the human already rejected. stdlib-only (`difflib.
  SequenceMatcher`, no embeddings, no new dependency — matches this project's zero-new-deps
  discipline throughout the session). Requires the file part of `file_symbol` to match exactly
  AND combined `file_symbol + description` similarity >= 0.86 — deliberately scoped to the
  later/never fallback path only, not the open-bucket bump path, since a false merge there would
  silently blend two distinct open findings (higher-stakes than a missed later/never catch).

Test suite at landing: 988 passed / 4 known pre-existing failures (cursor-agent CLI binary
absent on this machine, unrelated).

**Same pass, continued once the user confirmed headroom on the 5-hour rate limit ("использовали
только 60%")** — pre-fleet plan-approval checkpoint (RESEARCH_BACKLOG.md's largest remaining
high-priority item; the agent assigned to it in the previous stretch had failed on rate-limit
before writing any code). Deliberately implemented MUCH smaller than the original spec: instead
of a new `JobStatus.PAUSED_PLAN_APPROVAL` + `Job.pending_plan` column + new Telegram callback
buttons, it reuses the **already-existing** `ctx.ask_user`/`app/tasks/clarify.py` free-text Q&A
mechanism (the same one `ВОПРОС:` uses in the generic pipeline) — same PAUSED_QUESTION status,
same MessageHandler, same timeout/cancel handling, zero new schema. `Step5FleetPlanner`
(`app/tasks/protocol_full.py`) now asks for confirmation of its domain split after generating it
— reply "да"/empty/timeout keeps the plan, a comma-separated domain list replaces it. Gated to
`ctx.job.provider_mode == ProviderMode.MANUAL` only — autocheck/nightly runs (`ProviderMode.
AUTO`) have no human present to answer, so they'd otherwise block for the full 30-minute timeout
on every unattended run for nothing; those skip the checkpoint entirely and use the
fleet-planner's own domains, unchanged from before this feature. 5 new tests in `tests/
test_full_check_plan_approval.py` (skip-for-AUTO, skip-without-application, default-accepts,
custom-answer-overrides, timeout-falls-back), following the exact test pattern already
established for `ВОПРОС:` in `tests/test_generic_clarify.py` (monkeypatch `clarify.time.sleep`
to inject the answer, `SimpleNamespace`+`AsyncMock` for a fake `application`).

Test suite at this landing: 993 passed / same 4 known pre-existing failures. Bot restarted and
confirmed live (parent+child pair, clean startup log) — the critic-tier change and dedup fallback
were pure logic (no restart strictly required) but the plan-approval checkpoint does change live
runtime behavior for the next manual Full ЧЕК run, so a restart was done for this whole batch.

**Same pass, continued once more** — confidence indicator on the fix (🟢/🟡/🔴), the cheapest
remaining medium-priority item. Deliberately NOT a self-rated LLM confidence score (unreliable,
costs an extra call) — instead reads the real signal the pipeline already computes for free:
how many `Step11ConvergenceLoop` rounds it took the two critics to agree
(`ctx.state["convergence_rounds"]`/`"escalated"`, already persisted to `Job.state_json` by
`Pipeline.run` — no new column needed). `app/bot/formatting.py::_confidence_badge` + wired into
`render_report_header`, which already prefixes every completion message shown right above
`report_menu`'s buttons — a better fit than stuffing it into the keyboard itself (button labels
have no room for it and it'd be less readable there), a deliberate deviation from
RESEARCH_BACKLOG.md's original speculative "куда" note now corrected in that file. Empty string
(no badge at all) for any job that isn't Full ЧЕК, or has no `state_json` yet, or has malformed
JSON — never fabricates a confidence signal where the pipeline didn't actually compute one. 5 new
tests in `tests/test_formatting.py` (no-state, green/yellow/red, malformed-JSON-safe); one test
gotcha worth remembering — `TASK_TYPE_LABELS[TaskType.CHECK_FULL]` already starts with "🔴 Full
ЧЕК", so a naive `"🔴" not in text` assertion for the no-badge case false-fails on the task
label's own emoji, not the badge; asserted on the badge's distinguishing phrases instead.

Test suite at that landing: 998 passed / same 4 known pre-existing failures.

**Same pass, continued once more after the user asked for a recommendation between the 2
remaining large items** — picked budget-aware fleet sizing over full crux-isolation critic
exchange (reasoning given to the user: directly answers this session's own recurring pain point
about quota running out, reuses 100% existing infrastructure — circuit breaker, real quota
headers, `WARN_THRESHOLD_PCT` from `quota_warnings.py` — rather than inventing a new mechanism,
and doesn't touch `Step10Critics`/`Step11ConvergenceLoop` a third time in one day). `app/tasks/
protocol_full.py::_delegation_budget` counts how many DELEGATION-tier accounts are actually
usable right now (not disabled, not in circuit-breaker cooldown, real quota headroom below the
same 85% threshold `quota_warnings.py` already uses) — only meaningful when tier routing is
actually active for the job (registry present AND (job override OR global delegation mode)),
otherwise a no-op since every checker call falls back to the same shared `ctx.provider` anyway
regardless of domain count. `Step5FleetPlanner` clamps `target_domains` down to the usable count
(floor of 1, never scales UP past the default even with many healthy accounts — the goal is
protective, not "go faster") and explains the cut both in the fleet-planner's own prompt and in
the pre-flight approval question from the same pass, which compose naturally together. The note
also surfaces in `Step13HumanConfirm`'s final report. 6 new tests in `tests/
test_full_check_fleet_budget.py`.

Then, since the recommendation write-up already fully scoped what a *bounded* slice of the other
option would look like, implemented that cheap slice too rather than leaving it fully on the
table: in `Step11ConvergenceLoop`'s retry rounds, each critic now sees the OTHER critic's opinion
from the previous round (`_run_critic` gained an `other_opinion` parameter) and is asked to state
disagreement explicitly — captures most of the "critics never see each other's disagreement"
gap from arXiv:2607.01251 without the harder, riskier part (extracting a specific disputed
file:line via its own LLM call, i.e. true crux-isolation) — that harder part stays in
RESEARCH_BACKLOG.md as its own separate, still-open item. `Step10Critics` (first pass) doesn't
get this — nothing to exchange yet on round zero. 2 new tests in `tests/
test_full_check_critic_exchange.py`.

**Self-caught process error worth recording**: while doing all of the above, noticed partway
through that several of this pass's own new functions (`_delegation_budget`, `_is_reworded_duplicate`,
`_confidence_badge`, plus a couple of inline comments) had docstrings/`#` comments — a direct
violation of this session's own standing, STRENGTHENED "no comments in code, only in .md files"
rule (see `LAST_PROMPT.md`/memory `feedback_no_code_comments.md`). Went back and stripped every
comment/docstring added in this whole continuation (both production code and the new test files),
verified nothing pre-existing was touched, reran ruff+tests after the cleanup. Flagging this
plainly rather than quietly fixing it — it's a real lapse in following an explicit, repeated
instruction, not a one-off typo.

Test suite at that landing: 1006 passed / same 4 known pre-existing failures.

**Same pass, continued once more (user: "продолжи работу, ток 70% потратил")** — investigated
the last medium-priority item, "learn from what actually stuck" (Qodo Merge-inspired: reinforce
findings whose fixes actually got committed, not just track what got dismissed). Concluded it
is NOT a cheap win and deliberately did not implement it: `Step9Fixer`'s output is ONE combined
diff/prose fix for the whole report, not a per-finding patch (`Step13HumanConfirm`'s own
docstring already says as much), so a successful "Фикс всё" commit does not actually prove every
registered finding in that report was individually addressed — crediting all of them as
"validated" would be exactly the kind of fabricated/estimated signal this session has repeatedly
moved away from (real quota headers over self-estimates, real convergence-loop rounds over a
self-rated confidence score). A honest version needs either a real per-finding patch format
(a meaningful `Step9`/`Step13` redesign) or a coarser job-level acceptance-rate signal too blunt
to usefully inform the fleet-planner. Documented this reasoning directly in RESEARCH_BACKLOG.md
rather than silently skipping it.

Picked up a different, already-qualified low-priority item instead — "undo last agent's changes"
via one button — since its only stated blocker (a proper confirm-gate) is now trivial to satisfy
by reusing an existing pattern in this very file (`prompt_delete_project`'s `confirm_row` gate).
`app/tasks/patch_apply.py::discard_uncommitted_changes` runs `git checkout -- .` (tracked files
only — deliberately NOT `git clean`, leaves any new untracked files alone); new button "🗑️
Откатить незакоммиченные правки" in the project card (`app/bot/handlers/projects.py`), same
confirm-gate shape as project deletion. Not tied to a specific agent run — a general "discard
everything uncommitted in this project" action, covering both native-agent edits (`ai_chat`'s
`run_native_agent`, which really can touch files with `can_edit_code`) and any other source of
stray changes, complementing the existing `stash_check` safety net (`Step12TestWriter`) with a
symmetrical "undo" rather than just "warn." Shows "nothing to discard" instead of offering the
destructive action when the working tree is already clean. 7 new tests (`tests/
test_patch_apply.py` additions + new `tests/test_discard_changes.py`, following the exact
`_manual_push_blocking`-style pattern already established for this file's blocking helpers).

Final test suite for this whole session-continuation: 1013 passed / same 4 known pre-existing
failures (cursor-agent CLI absent, confirmed unrelated via a Git-Bash rerun where the earlier
grep-PATH artifacts also disappear). Bot restarted again, confirmed live, clean startup log.

RESEARCH_BACKLOG.md is now down to: full crux-isolation (the harder, deliberately-deferred part
of critic exchange) plus the remaining low-priority/speculative list (each already carrying its
own documented reason for not doing it as-is).

**New user request mid-session, not from the original research agents** — "почему нет реальных
% лимитов... в вскоде видно... улучши удобство меню... сделай тест работоспособности апи, кидай
сообщение". Split into a background research task (real Claude Code quota %) run in parallel
with two self-directed implementations:
- **Escalation crux summary** — small follow-up to the critic-exchange feature: when
  `Step11ConvergenceLoop` actually escalates (never converges), one extra LLM call
  (`_summarize_disagreement`) names the specific disputed point, surfaced in both the final
  report and the 🔴 confidence badge. Only fires on the rare escalation path, not every round.
- **Health monitor** (`app/providers/health_check.py` + `app/scheduler/health_monitor.py`,
  30-min tick) — active cheap probe (`max_tokens=4` ping) for the 10 HTTP/API-key providers;
  deliberately NO active probing for `claude_code`/`cursor`/`codex` (`NO_ACTIVE_PROBE`) since
  that would burn real subscription-window quota just to check status — directly against this
  whole session's quota-preservation theme. Those 3 get passive-only monitoring from real
  circuit-breaker trips during actual job calls, and deliberately never send an automatic
  "recovered" message (circuit-breaker cooldown expiring is a timer, not a verified recovery —
  claiming recovery from that would be exactly the fabricated-signal pattern this session has
  repeatedly avoided). Also added a 🔴 indicator next to accounts in the `📊 Лимиты` screen
  (`app/bot/handlers/menu.py`), reusing the same circuit-breaker signal. 22 new tests combined.
- **Real Claude Code quota % — researched, NOT implemented.** Background agent confirmed: no
  official non-interactive way exists; the real number comes from an undocumented endpoint
  (`GET api.anthropic.com/api/oauth/usage`, requires a spoofed `User-Agent: claude-code/<ver>`)
  that powers the VSCode extension's/CLI's own `/usage` display. Two real blockers found: (1) it's
  unofficial — could break or start 429ing without notice; (2) this bot's `CLAUDE_CODE_OAUTH_TOKEN`
  (used for every extra claude_code account) only carries `user:inference` scope, not the
  `user:profile` scope the endpoint requires — a confirmed, filed Anthropic bug
  (`anthropics/claude-code#22450`), not fixable client-side — so even if implemented, it could
  only ever work for the primary claude_code slot (real `/login` session), never the extras.
  Cursor/Codex have no reliable headless-usage path at all (confirmed upstream issues for both).
  Full findings in RESEARCH_BACKLOG.md item 5 — deliberately left as a decision for the user
  rather than silently implementing something fragile/ToS-adjacent.

Test suite: 1028 passed / same 4 known pre-existing failures. Bot restarted, confirmed live.

**User answered the real-% question: "Да, сделай"** — implemented the fragile path, scoped
exactly to what was proposed (primary `claude_code` account only, marked experimental).
`app/providers/claude_code_usage.py::fetch_real_usage(cli_path)` — reads the OAuth access token
from `~/.claude/.credentials.json` (only if its `scopes` include `user:profile` — otherwise
silently returns `None`, never attempts the call with an insufficient token), calls `GET
https://api.anthropic.com/api/oauth/usage` with a spoofed `User-Agent: claude-code/<version>`
(the real installed CLI's own version, fetched once via `claude --version` and cached for the
process — not hardcoded, so it won't silently drift stale across a CLI upgrade), 180s cache per
the research's own throttling recommendation. Picks whichever of `five_hour`/`seven_day` has the
higher utilization as the "binding" constraint (the one that will actually block the next call
first) and maps it onto the *existing* `QuotaEstimate(is_estimate=False)` contract — zero new
consumer-facing type, every existing caller (limits screen, quota warnings, progress line) just
gets better data for free. **Never raises** — any failure (network, 401, 429, malformed JSON,
missing file, missing scope) returns `None` and the caller falls straight back to the
pre-existing self-estimate; this was a hard requirement, not an incidental nicety, since the
whole point is the real check is additive, not a new failure mode.

Wired into `app/providers/quota.py::account_quota_estimate_for` — tried FIRST, before the
existing rate-limit-header/self-estimate fallback chain, but ONLY for `(ProviderName.CLAUDE_CODE,
"primary")` — extra accounts (`CLAUDE_CODE_OAUTH_TOKEN`/`setup-token`) are never even attempted,
since the research confirmed they structurally lack the required scope. Surfaced with an explicit
🧪 "экспериментально/неофициальный эндпоинт" label, kept deliberately distinct from the existing
"реальные данные API" label used for the sturdy, officially-documented HTTP rate-limit-header
case (`app/scheduler/quota_warnings.py`'s `source_note`, `app/bot/handlers/menu.py::limits_text`'s
per-account line) — the two "real" sources have very different stability guarantees and showing
them identically would have been misleading. New autouse fixture in `tests/conftest.py` resets
`claude_code_usage`'s module-level cache between tests (same pattern as the existing
circuit-breaker reset). 22 new tests across `tests/test_claude_code_usage.py` (the fetch function
itself: no-file/no-scope/no-cli-path/success/cache/HTTP-error/malformed-JSON/no-window/malformed-
credentials/version-fallback) plus additions to the three existing quota-display test files.

Final test suite: 1045 passed / same 4 known pre-existing failures. Bot restarted, confirmed live.

**Stopped here at explicit user request ("стоп, закончи то что щас делаешь").** This item (real
Claude Code %) is the last thing in flight and IS finished — implemented, tested, ruff-clean, bot
restarted live. Nothing was left mid-edit. RESEARCH_BACKLOG.md is now down to only the two
deliberately-deferred items (full crux-isolation, "учимся на прижившемся") plus the low-priority/
speculative list — see that file for exact current state, it's kept accurate as items land.
Nothing has been committed/pushed in ai-check-bot itself this entire session-continuation — that
question was never re-asked after the user stopped answering it several HANDOVER entries back;
worth asking directly next time rather than assuming either way.

# SESSION LOG

--- 2026-08-28 МСК - Job control, AI questions, tier resilience, real quota, agent perms, custom APIs, web tools ---
Single very long session (same day as the previous "Cross-provider quota fallback"
entry below — continuation). User's asks arrived as a long, fast, mid-turn stream
rather than one upfront spec — see the numbered subsystems above (Job control /
AI-asks-a-question / Cross-provider resilience / Real quota data / Custom API slots /
Agent permissions / Other new pieces) for the technical detail, this entry is the
narrative. Roughly in order: (1) job pause/stop already existed from an earlier
session — added 💬 live comments and 📦 archive-to-file on top; (2) AI can now ask a
mid-step clarifying question that reaches Telegram and blocks for a real answer
(generic pipeline only); (3) 🧠 thinking-level + 📊 "AI sees its own quota" settings;
(4) per-provider ✏️ edit-code/🐙 push-to-github permissions, closing a real gap in
`claude_code_cli.py` that had NO github-token gate at all before now; (5) 3 custom
OpenAI/Anthropic-compatible API slots, configurable from the bot, with real
rate-limit-header quota scraping; (6) researched several open-source LLM
gateways/routers (LiteLLM, Bifrost, Portkey, OpenRouter — see chat log for sources)
specifically because the user was worried about "what happens when the HEAD account
runs out" — adopted circuit-breaker (5-min cooldown skip) and generalized the
rate-limit-header scraping to ALL 10 `OpenAICompatibleProvider` subclasses, plus made
multiple same-tier accounts actually retry each other before giving up (previously
tried exactly one tier account, silently fell back to the job's default provider);
(7) proactive quota-threshold Telegram warnings; (8) `send_message`/`send_file`
(path-traversal-safe)/`web_search` (DuckDuckGo, keyless)/`fetch_url` (stdlib-only)
added to the 🗨 chat's tool allowlist.
Delegated 3 background Sonnet agents for test-writing only (same successful pattern as
the previous session's chain-fallback tests) — all correctly scoped, zero production
edits from any of them, ~340 new tests combined. They found 3 real bugs I fixed
directly: `clarify.py` unconditionally forced `RUNNING` on a send failure instead of
guarding with `if status == PAUSED_QUESTION` (could stomp a status set by a concurrent
change); `clarify.ask_and_wait`'s `timeout` parameter defaulted from the module
constant at DEF time, so monkeypatching the constant in a test (or overriding it any
other way) silently did nothing — changed the default to `None` + resolved inside the
function body; `custom_api.py`'s OpenAI-format response parsing had no guard against a
malformed 200 response (missing `choices`/null `content`), raising a raw
`KeyError`/`TypeError` instead of `ProviderError` and breaking the fallback contract
every caller relies on. Also found and fixed myself, NOT by an agent: my own first
draft of `can_push_github` defaulted to `False` for every provider, which silently
broke a PRE-EXISTING test (`test_cursor_github_token_env.py`) asserting the OLD
single-global-toggle behavior — this turned into the actual "главная нейронка
пушит по умолчанию" design (see Agent permissions above), not just a revert; a
`parse_duration_to_hours` edge case where a genuinely-zero duration like `"0s"` was
indistinguishable from "couldn't parse" and silently became `None`; and my own
`circuit_breaker.py`'s module-level dict leaking state across pytest test functions
within the same run (fixed via the autouse `conftest.py` fixture noted above — this
one I caught myself, from my OWN new tests failing against EACH OTHER's prior state,
not a pre-existing test).
User was explicit and repeated about "без комментов в коде, ток в мдшках" this
session (stronger than the prior session's version — no carve-out for a project's own
comment-heavy pre-existing style) — every new file this session is comment-free;
pre-existing code from earlier sessions was left as-is, not retroactively stripped.
User also explicitly chose (`AskUserQuestion`) the "eventual full string-ID refactor"
answer for the custom-API-slots scaling question, while accepting 3 fixed slots as the
interim state for now — that refactor is tracked above, not started.
Test status: 911 passed at session end (was 679 at session start), same 4
pre-existing unrelated cursor-agent-CLI Windows failures (`WinError 193`, no
`cursor-agent` binary on this machine) throughout, never touched. Ruff clean
throughout. Bot restarted and confirmed live (parent+child process pair) after the
full change, `PRAGMA table_info` confirmed the new `Job.live_notes`/
`Job.pending_question` columns present in the live `data/bot.sqlite3` with no manual
migration step.
Not done / explicitly deferred, all noted above at the relevant subsystem: full
string-ID provider refactor (unlimited custom slots); weighted (non-uniform)
tier-account load balancing — still plain round-robin; real Anthropic-native-API
quota headers (`ClaudeProvider` still self-estimate only); `ВОПРОС:` question-asking
wired into only the generic pipeline, not protocol_full/lite; no manual/live
Telegram click-through of any new screen this session, verification was all via
pytest at the handler-function level.

--- 2026-08-27 МСК (продолжение) - Cross-provider quota fallback ("работает без остановок") ---
User request: "подготовь проект к работе без остановок, типа дал задачу, а он шпарит до
момента пока не закончатся все лимиты, ток тогда он встанет и будет ждать пока один из
акков не обновится." Root cause found: `router.fallback_chain()` existed but was dead
code — a job picked ONE provider at start and stayed on it for the whole run; hitting
quota (after that provider's OWN multi-account fallback was already exhausted) paused
the ENTIRE job even with other connected providers available. Built `ChainFallbackProvider`
(new file `app/providers/chain_fallback.py`) wired into `ctx.provider` in job_runner.py;
`run_prompt_with_tier` now falls through to `ctx.provider` when a tier-assigned account
fails (not just when nothing's assigned); `autocheck.py::_resume_tick` now checks
availability across the WHOLE fallback chain, not just `job.provider`'s own quota
estimate — see "Cross-provider quota fallback" writeup above for the full mechanics and
the mid-run session-staleness bug found and fixed along the way (`session.refresh`).
Two Haiku subagents wrote+verified `tests/test_chain_fallback.py` and the additions to
`tests/test_tiers.py`/`tests/test_autocheck_tick.py` in parallel (found no bugs in the
reviewed production code — both correctly scoped to test-writing only, no production
edits); the job_runner.py session-staleness fix and its regression test
(`tests/test_job_runner.py`) were done directly, not delegated, and the regression test
was verified red-then-green before being kept. 675 passed total, same 4 pre-existing
unrelated cursor-agent Windows failures. Ruff clean. Bot restarted and confirmed live
after the full change.
User also gave two pieces of standing feedback this session: (1) Agent-tool subagents —
Haiku by default, Sonnet only for genuine parallelization or a real tooling gap, never
just "this one task is hard" (already in cross-project memory, not repeated in full
here); (2) NEW — no explanatory comments in code Claude writes going forward (applied to
this session's own new/touched files: chain_fallback.py, the tiers.py/autocheck.py
additions, job_runner.py's new lines — stripped after the instruction arrived; did NOT
retroactively strip comments from this project's EARLIER pre-existing code or from
before-this-instruction code in this same session, e.g. the per-job AI-selection wizard
above — that code stays commented as originally written).

--- 2026-08-27 МСК - Per-job AI/tier selection wizard step ---
User request: "сделай функцию что бы при любой работе с проектами можно было выбрать
какие иишки будут работать с этой задачей... как и комментарии к задаче пусть будет
список с иишками которые будут работать с проектом и приоритет на этом этапе... если
не поставил приоритет на какую то ии то значит не используем её в задаче, если не задал
ни одного приоритета то значит используем настройки из настроек." Design reference
requested from MeCelium's agent-delegation approach (DELEGATION.md/TOKEN_ECONOMY.md —
explicit role→model routing, tiered capability, fallback-to-default ladder), adapted
here as a per-job override layered on top of the EXISTING account-tier system rather
than a new mechanism. Implemented: `JobAccountTierAssignment` table (new, `create_all()`
picks it up automatically — no `_MISSING_COLUMNS` entry needed, brand-new table not a
column); `tiers.py` gained `job_tier_assignments`/`job_has_tier_overrides`/
`set_job_tier`, `TierPicker(job_id=...)` builds its account pool from job overrides
instead of global `AccountTierAssignment` when a job_id is given, `run_prompt_with_tier`
picks job-override vs. global based on `job_has_tier_overrides` (job override also
bypasses the global `delegation_mode_enabled` toggle, see architecture section above);
new wizard screen `app/bot/handlers/check.py::_ai_picker_view` between comment and
confirm (all 5 task types, both CHECK and non-CHECK branches) — cycle-tap per account
exactly like the existing ⚙️ Настройки → 🎚 Приоритеты аккаунтов screen (`TIER_ICON`/
`TIER_RU_NAME`/`TIER_CYCLE` de-duplicated into `tiers.py`, `settings_admin.py` now
imports them instead of a private local copy); `confirm()` writes the overrides via
`set_job_tier` right after `queue.enqueue()`, synchronously before `start_job` is even
scheduled (no race). Explicitly OUT of scope: `_enqueue_fix` (the "🔧 Применить фикс"
quick-reenqueue flow from a report) does NOT get the picker — always falls back to
global settings, same as before this feature; user wasn't asked whether that gap
matters, worth surfacing if per-job AI selection is expected there too.
Found and fixed a related LATENT bug while extending this same subsystem, not part of
the original request: `ctx.state["_tier_picker"]` (a live object, not JSON-safe) would
have survived a job resume as a garbage string via last session's `default=str` safety
net, then crashed the next `run_prompt_with_tier` call post-resume calling `.pick()` on
a `str`. Fixed by excluding `_`-prefixed `ctx.state` keys from persistence entirely (see
architecture section above) — this is now a documented convention for any future
runtime-only state, not just this one key.
Tests: `tests/test_tiers.py` (+5: job override CRUD, `TierPicker(job_id)` isolation,
`run_prompt_with_tier` job-override-bypasses-toggle and job-override-excludes-
non-overridden-account), `tests/test_pipeline.py` (+1: `_`-prefixed state never
persisted/restored, doesn't crash resume), `tests/test_check_flow_navigation.py`
(+6: new screen navigation/cycle/summary; also fixed 1 pre-existing test asserting the
old `confirm_menu` back-target, a real consequence of inserting the new wizard step, not
a regression). 661 passed, same 4 pre-existing unrelated cursor-agent Windows failures.
Ruff clean throughout. Bot restarted and verified live after the full change (confirmed
`job_account_tier_assignments` table present in the live `data/bot.sqlite3` via
`create_all()`, no manual migration needed).

--- 2026-08-26 МСК (вечер) - Оба известных open-бага из chek_open.md закрыты ---
Продолжение предыдущей сессии по её же LAST_PROMPT.md. Fixed both bugs left open from
the previous session, chek_open.md now empty: (1) pipeline resume-from-scratch — added
`Job.state_json` column, `Pipeline.run` now persists `ctx.state` after every completed
step and restores+skips on resume instead of redoing steps 1..N from an empty state (see
"HANDOVER/resume persistence" writeup above); (2) `accounts_store.remove_extra_account`
account_label renumbering — deleting a non-last extra account now correctly shifts
AccountTierAssignment/ProxyAssignment rows to follow the surviving physical accounts (see
writeup above). Both: ruff clean, full test suite (649 passed, same 4 pre-existing
unrelated cursor-agent Windows failures throughout), bot restarted and verified live after
each fix (schema migration for state_json confirmed applied to the live data/bot.sqlite3).
Process note: attempted to delegate bug (2) to a background subagent in an isolated git
worktree — failed, because this repo currently has a large amount of real feature code
(AccountTierAssignment/ProxyAssignment models, tiers.py, proxy consumer wiring, ~40 test
files) that is NOT YET COMMITTED to git (worktree branch and `project-creation` both point
at the same old commit `c284e3d`, way behind the working tree). A worktree only checks out
committed history, so the agent got a stripped-down repo missing the very code it needed
to fix and correctly refused to improvise — it made no changes. Fixed (2) directly in the
main checkout instead once this was diagnosed; the worktree/branch it created were cleaned
up (`git worktree remove` + `git branch -D`, no changes were in them). Flagging for
whoever next considers a big commit: this project has apparently been running with a large
uncommitted diff for a while — worth knowing before assuming `git log`/`git diff main`
reflects current reality, and before trying worktree-isolated delegation again here.

--- 2026-08-26 МСК - Tiers + proxies + model switching + 🗨 group AI-chat + native agents + menu UX audit ---
Single very long session, chronological: (1) account priority tiers (HEAD/MEDIUM/
DELEGATION) wired into all three pipelines + Settings UI; (2) proxy pool sourced from
MeCelium DB + manual ss:// paste, Xray bridge, health-check auto-replace, wired into
every provider's account+proxy pairing; (3) per-provider live model override
(app/providers/model_store.py); (4) 🗨 Групповой ИИ-чат from scratch — shared-context
multi-account chat, text-protocol tool calling, delegate + run_native_agent tools,
chat history/resume (AiChatSession reopened, not recreated), live status-message
progress; (5) fixed a real production bug found mid-session: `reconcile_orphaned()`
silently error'd jobs killed by a bot restart with ONLY a log line, no Telegram
notification — user waited on a job that had actually died; now notifies via
`notify_admin`; (6) 9-agent parallel menu/UX audit (76 findings) — fixed all 17 high
severity (stale awaiting flags leaking between flows including into the brand-new
ai-chat, markdown-escape crashes, dead-end nav, missing confirm gates before
disabling delegation/clearing GitHub token, GitHub open-admin-mode token bug that
silently dropped every token paste) plus most medium/low findings (pagination gaps on
proxies/repos, `nav_row` vs bare `back_button` consistency, wording). Two real bugs
identified but NOT fixed this session, tracked in chek_open.md instead: pipeline
resume-from-scratch, and positional account_label instability on non-last extra
account deletion. 644 tests passing throughout (same 4 pre-existing unrelated
cursor-agent Windows failures the whole session). Bot restarted and verified live
after every meaningful change — this session was the first to also bootstrap this
project's OWN md-continuity files (this file, STATE_LOG.md, chek_open.md,
LAST_PROMPT.md) after the user pointed out they didn't exist yet, despite the bot
existing specifically to run this protocol FOR other projects.
