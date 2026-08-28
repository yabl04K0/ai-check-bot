# STATE_LOG — append-only machine log of runtime state (LLM-ONLY, English on purpose)

FORMAT: LLM only. Flat text, `key: value`, facts. NOT for humans — no decoration, no tables, no prose.
APPEND: new entries go AT THE END. Never rewrite an old entry (the log is history).
ENTRY: each entry starts with `--- [PREFIX] YYYY-MM-DD HH:MM МСК (HH:MM UTC) ---`,
then `key: value` lines.

# === entries below (append) ===

--- [HANDOVER] 2026-08-26 18:06 МСК (15:06 UTC) ---
session_scope: huge multi-feature session: account priority tiers, proxy pool, model switching, 🗨 group AI-chat, native Claude Code agents, chat history/resume, live progress visualization, full menu UX audit
done_tiers: AccountPriority enum (HEAD/MEDIUM/DELEGATION) + AccountTierAssignment model, app/providers/tiers.py (TierPicker round-robin, run_prompt_with_tier), wired into protocol_full/protocol_lite/generic pipeline steps, Settings UI (cycle-tap), claude_code auto-seeded to HEAD on startup
done_proxies: ProxyPoolEntry/ProxyAssignment models, MeCelium import + manual ss:// import, Xray SOCKS5 bridge, health-check with auto-replace, per-account proxy assignment for all 10 OpenAICompatible providers + claude_code_cli
done_model_switching: app/providers/model_store.py, per-provider live model override via bot UI (Settings -> Key -> Change model)
done_ai_chat: app/ai_chat/{orchestrator,tools,sessions,approvals}.py + app/bot/handlers/ai_chat.py — 🗨 group chat with shared context across multiple AI accounts, text-protocol tool-calling (DEJSTVIE: name | args, works uniformly across all 13 providers), delegate tool (cross-account/cross-tier text delegation via call_tier_account), run_native_agent tool (real Claude Code CLI agent with --permission-mode bypassPermissions on own subscription, gated by ai_native_agents_enabled toggle + per-task approval via ai_command_auto_approve_enabled), chat history list + resume-old-session (AiChatSession.status_detail live progress field)
done_menu_audit: 9-agent parallel review found 76 UX findings across all handler files; fixed all 17 high-severity (stale awaiting flags leaking into ai-chat/other flows, markdown-escape crashes, dead-end screens, missing confirm gates, GitHub open-mode token bug) plus most medium/low (pagination gaps, nav_row consistency, wording)
known_open_bugs: see chek_open.md: (1) Pipeline.run always restarts from step 1 on quota-resume instead of continuing from progress_step, (2) accounts_store.remove_extra_account does not renumber AccountTierAssignment/ProxyAssignment positional labels on non-last-account deletion
test_status: 644 passed, 4 pre-existing unrelated cursor-agent Windows failures (WinError 193, no TTY) untouched all session
bot_process: restarted after every change; last restart log /tmp/aicheckbot_run15.log, stable
user_note: user asked (2026-08-26) whether the md-file continuity protocol (used in MeCelium/other sibling projects) was being followed for ai-check-bot itself — it was NOT; this HANDOVER + chek_open.md + PROJECT_MEMORY.md + LAST_PROMPT.md were created retroactively to bootstrap it for this project

--- [HANDOVER] 2026-08-26 21:50 МСК (18:50 UTC) ---
session_scope: follow-up session per previous HANDOVER's LAST_PROMPT.md — fixed both known open bugs, chek_open.md now empty
done_pipeline_resume: added Job.state_json column (app/db/models.py + app/db/session.py _MISSING_COLUMNS); Pipeline.run (app/tasks/pipeline.py) persists ctx.state as JSON after every completed step, restores it and skips already-done steps on resume instead of restarting from step 1; regression test tests/test_pipeline.py::test_pipeline_resume_skips_done_steps_and_restores_state
done_accounts_store_renumber: app/providers/accounts_store.py::remove_extra_account now shifts AccountTierAssignment/ProxyAssignment account_label down by one for every extra account above the removed position, one shift at a time with session.flush() between each (SQLAlchemy does not order same-table UPDATEs within one flush, needed to avoid a transient UniqueConstraint(provider,account_label) collision); new tests/test_accounts_store.py (4 tests: last-position no-shift, middle-position shifts tier+proxy correctly, no-assignment account, wrong-provider/unknown-id no-op)
process_note: tried delegating the accounts_store fix to a background subagent in an isolated git worktree (isolation:"worktree") — failed, this repo has a large amount of real feature code (AccountTierAssignment/ProxyAssignment models, tiers.py, proxy wiring, ~40 test files) NOT committed to git (worktree branch and project-creation both at c284e3d, far behind the working tree); the worktree only saw committed history and correctly refused to improvise instead of diverging. Cleaned up (git worktree remove + git branch -D, agent made zero changes) and fixed directly in the main checkout instead.
test_status: 649 passed, same 4 pre-existing unrelated cursor-agent Windows failures (WinError 193, no cursor-agent CLI on this machine) untouched
bot_process: killed old parent+child pair, restarted venv/Scripts/python.exe -m app.main after EACH fix, confirmed live both times; state_json column confirmed present in live data/bot.sqlite3 via PRAGMA table_info after the first restart
not_done: nothing committed/pushed (user paused mid-session with "стоп" before any commit was requested); no fresh ЧЕК run against these changes, just the two targeted regression tests + full suite

--- [HANDOVER] 2026-08-27 00:20 МСК (21:20 UTC) ---
session_scope: new feature — per-job AI/tier selection wizard step, requested by user, design pattern taken from MeCelium's agent-delegation approach (DELEGATION.md/TOKEN_ECONOMY.md) adapted onto ai-check-bot's existing AccountPriority tier system
done_feature: JobAccountTierAssignment table (new, auto-created via create_all()); tiers.py: job_tier_assignments/job_has_tier_overrides/set_job_tier, TierPicker(job_id=...), run_prompt_with_tier now checks job override first (bypasses global delegation_mode_enabled toggle when a job override exists) then falls back to global AccountTierAssignment exactly as before; new wizard screen app/bot/handlers/check.py::_ai_picker_view between comment and confirm, cycle-tap UI matching Settings' tier screen (TIER_ICON/TIER_RU_NAME/TIER_CYCLE moved from settings_admin.py private copies into tiers.py as shared public constants); confirm() persists overrides via set_job_tier synchronously right after queue.enqueue(), before start_job is scheduled — no race
done_related_fix: found+fixed a latent bug in the SAME subsystem while extending it — ctx.state["_tier_picker"] (live object) would have survived a resumed job as a garbage string (last session's json.dumps default=str), crashing the next run_prompt_with_tier call post-resume; fixed by excluding "_"-prefixed ctx.state keys from persistence in Pipeline.run, now a documented convention for future runtime-only state
scope_note: _enqueue_fix (quick re-run of a FIX job from a report screen) does NOT get the AI picker — stays on global settings only, same as before this feature; not asked whether that gap matters, flag if per-job selection is expected there too
test_status: 661 passed (was 654 at previous handover — +5 test_tiers.py, +1 test_pipeline.py, +6 test_check_flow_navigation.py, net +12 counting 1 pre-existing test fixed for the new confirm_menu back-target, not a regression), same 4 pre-existing unrelated cursor-agent Windows failures untouched
bot_process: restarted after the full change, confirmed live (parent+child pair), confirmed job_account_tier_assignments table present in live data/bot.sqlite3 via PRAGMA table_info — create_all() picked it up with no manual migration step needed
not_done: nothing committed/pushed; no manual end-to-end Telegram click-through of the new wizard screen this session (unit/handler-level tests only, no real Application/bot running the actual conversation)

--- [HANDOVER] 2026-08-27 13:30 МСК (10:30 UTC) ---
session_scope: cross-provider quota fallback ("работает без остановок пока не кончатся все лимиты") — new app/providers/chain_fallback.py wired into ctx.provider (job_runner.py), run_prompt_with_tier falls through to ctx.provider on tier-account failure, autocheck.py::_resume_tick checks the whole fallback_chain not just job.provider
done_bug_fix: found+fixed a real cross-session staleness bug in job_runner.py while building this — ChainFallbackProvider._persist_switch writes job.provider via its own short session (thread-safety, same reason as NoteTrackingProvider); ctx.session's long-lived job object doesn't see that write automatically, so HistoryEntry.provider would have recorded the ORIGINAL provider after a mid-run switch; fixed with session.refresh(job, attribute_names=["provider"]) right after pipeline.run() returns; regression test (tests/test_job_runner.py) verified red-then-green (reverted the fix, confirmed the test failed with the exact expected assertion, restored the fix, confirmed green) before being kept
done_delegation: two Haiku Agent-tool subagents ran in parallel writing tests/test_chain_fallback.py (7 tests) and additions to tests/test_tiers.py (+1)/tests/test_autocheck_tick.py (+5) — both correctly scoped to test-writing only per their prompts, found no production bugs, all ruff-clean and passing on first report
test_status: 675 passed, same 4 pre-existing unrelated cursor-agent Windows failures untouched
bot_process: restarted after the full change, confirmed live (parent+child pair)
user_feedback_this_session: (1) Agent-tool subagents should default to Haiku, Sonnet only for genuine parallelization or a real tooling gap — already tracked in cross-project memory; (2) NEW — no explanatory code comments going forward; applied to this feature's own new/touched files (chain_fallback.py, tiers.py/autocheck.py additions, job_runner.py's new lines) after the instruction arrived mid-session, NOT retroactively applied to earlier code in this same session or this project's pre-existing style
not_done: nothing committed/pushed; no manual end-to-end Telegram exercise of a real quota-exhaustion scenario (all verification via unit/integration tests with fake providers, no live provider actually ran out of quota this session)

--- [HANDOVER] 2026-08-28 МСК (11:40 UTC) ---
session_scope: huge multi-feature session, same day as previous HANDOVER — job control (live comments/archive-to-file), AI-asks-a-question mid-step, thinking-level + limits-visibility settings, per-provider agent permissions (edit/push), 3 custom OpenAI/Anthropic-compatible API slots, real quota data from HTTP rate-limit headers, circuit breaker, multi-account tier resilience, proactive quota warnings, web_search/fetch_url/send_message/send_file ai_chat tools — see PROJECT_MEMORY.md architecture sections (Job control / AI-asks-a-question / Cross-provider resilience / Real quota data / Custom API provider slots / Agent permissions / Other new pieces, all dated 2026-08-28) for full technical detail
done_job_control: Job.live_notes + Job.pending_question columns, JobStatus.PAUSED_QUESTION; queue.add_live_note; Pipeline.run refreshes+rebuilds ctx.comment from live_notes every step (cross-session staleness pattern, same fix class as the ChainFallbackProvider one from the previous HANDOVER); app/tasks/archive_export.py + job_runner.py::_send_handoff_document sends a markdown handoff doc via Telegram document on 📦 Архив
done_clarify: app/tasks/clarify.py (ask_and_wait, PAUSED_QUESTION, in-memory pending-answer dict) + app/bot/handlers/clarify.py (new MessageHandler group=8) + StepContext.ask_user; wired into GenericStep1Plan via a ВОПРОС: marker in its system prompt (same text-protocol trick as ai_chat's ДЕЙСТВИЕ:), NOT into protocol_full/lite yet
done_resilience: app/providers/circuit_breaker.py (5-min cooldown, in-memory, MUST be reset between tests via tests/conftest.py's new autouse fixture); TierPicker.pick_all() + run_prompt_with_tier/call_tier_account now retry ALL same-tier accounts (not just one) before falling back — multiple accounts on tier HEAD now genuinely act as one resilient neural network per user's explicit ask ("лимит главной иишки это реально проблема"); app/scheduler/quota_warnings.py proactive 85%-threshold Telegram warning, deduped, new APScheduler tick
done_real_quota: app/providers/rate_limit_headers.py (shared pure functions) scrapes x-ratelimit-*/anthropic-ratelimit-*/retry-after response headers into QuotaEstimate(is_estimate=False); wired into OpenAICompatibleProvider (10 subclasses) and CustomOpenAICompatibleProvider; app/providers/quota.py::account_quota_estimate_for is the new preferred per-account entry point (real header data > per-account DB estimate > provider-wide estimate); ClaudeProvider (native Anthropic API) and the CLI-based providers (claude_code_cli/cursor/codex) still self-estimate only — real headers not wired there
done_custom_api: app/providers/custom_api.py, 3 fixed slots (ProviderName.CUSTOM_1/2/3), configurable base_url/model/auth_style(bearer|x-api-key|none)/response_format(openai|anthropic) from the bot (⚙️ Настройки → 🤖 Настройки агентов → ➕ Свой API); user explicitly chose (AskUserQuestion) a future full string-ID ProviderName refactor as the eventual right answer for truly unlimited slots — NOT started this session, 3 fixed slots is the interim state
done_agent_permissions: app/providers/agent_permissions.py can_edit_code (default True, gates claude_code_cli --permission-mode bypassPermissions vs plan) and can_push_github (default: explicit override > HEAD-tier account > False — "не главным нейронкам нельзя гитхаб, главная сама пушит"); closed a real pre-existing gap: claude_code_cli.py never gated GITHUB_TOKEN on anything before this session (cursor.py always did) — a native agent could already git push using ambient machine credentials regardless of any bot toggle
done_other: app/providers/thinking.py + prompt_augment.py::PromptAugmentProvider (off/low/medium/high, force_limits for HEAD/orchestrator roles); app/providers/account_notes.py (per-account comment) + settings_admin.py::send_accounts_list (one Telegram message per connected account, per user's explicit requested shape); app/tasks/web_research.py + ai_chat tools web_search (DuckDuckGo keyless HTML endpoint)/fetch_url (stdlib html.parser, no new dependency)/send_message/send_file (path-traversal-safe, tested against both ../ and absolute-path escape)
done_research: web-searched several open-source LLM gateways/routers (LiteLLM, Bifrost, Portkey, OpenRouter, LobeChat/Open WebUI) per explicit user request ("чекай агрегаторы, бери фишки") — adopted circuit-breaker and real-header quota patterns from that research, did NOT verbatim-copy any third-party code (wrote fresh implementations inspired by the general patterns, which aren't copyrightable — only literal expression is)
bugs_found_and_fixed: 3 by delegated test-writing agents (clarify.py unconditional RUNNING revert on send-failure not guarding status; clarify.ask_and_wait timeout bound at def-time so monkeypatching the module constant silently didn't work, changed default to None resolved in-body; custom_api.py OpenAI-format parsing unguarded against malformed 200 response, raised raw KeyError/TypeError instead of ProviderError breaking the fallback contract) + 3 found by me during my own verification (my own first-draft can_push_github default=False regressed a pre-existing test, turned into the correct HEAD-tier-aware design not just a revert; parse_duration_to_hours couldn't distinguish a genuine "0s" from an unparseable string; circuit_breaker.py's own module-level dict leaking across pytest test functions, fixed via conftest.py autouse reset)
done_delegation: 3 background Sonnet Agent-tool subagents, all test-writing only (matches the model policy: Haiku default, Sonnet for genuine parallelization — 3 ran concurrently), zero production-code edits from any of them, ~340 new tests combined across all three
test_status: 911 passed, same 4 pre-existing unrelated cursor-agent Windows failures untouched (WinError 193, no cursor-agent CLI binary on this machine)
bot_process: restarted after the full change, confirmed live (parent+child pair); PRAGMA table_info confirmed Job.live_notes/Job.pending_question present in live data/bot.sqlite3, no manual migration step
user_feedback_this_session: "без комментов в коде, ток в мдшках" reiterated STRONGER than the previous session's version — no carve-out this time for a project's own pre-existing comment-heavy style; every new file this session is comment-free, pre-existing code from earlier sessions left as-is
not_done: nothing committed/pushed; full string-ID ProviderName refactor (unlimited custom API slots) — explicitly deferred by user's own choice; weighted (non-uniform) tier-account load balancing — still plain round-robin; ClaudeProvider real quota headers; ВОПРОС: question-asking only in generic pipeline, not protocol_full/lite; no manual/live Telegram click-through of any new screen — all verification via pytest at the handler-function level, not by tapping through the real bot UI

--- [HANDOVER] 2026-08-28 МСК (12:20 UTC) ---
session_scope: same-day continuation of the previous HANDOVER — user pushed back mid-session ("ты закончил разве с проектом?") after noticing two early asks never got built, plus rejected the 3-fixed-slots custom-API design as not matching "неограниченное кол-во слотов". Closed both gaps and redesigned custom API for real this entry.
done_compact: app/ai_chat/compact.py::maybe_compact — /compact analogue for 🗨 group chat, wired into orchestrator.run_turn's top. Threshold 600_000 chars (not literally 1M tokens — most connected providers cap well under that), keeps last 12 messages verbatim, summarizes the rest via one provider call, REUSES the first compacted message's row id (mutated in place) instead of inserting a new one so chronological order is preserved (AiChatMessage.id is an autoincrement PK, a fresh insert would sort last). Sends one Telegram notification with real before/after counts. ProviderError during summarization leaves history untouched, logged not raised.
done_activity_screen: 🤖 Активность (menu:activity) — app/bot/handlers/menu.py::activity_text/show_activity, new app/ai_chat/agent_activity.py (thread-safe in-memory start/finish/active() registry wired as pure instrumentation around run_agentic_task in _tool_run_native_agent), new app/ai_chat/sessions.py::sessions_with_live_status. Static render-on-open, no auto-refresh poll loop and no live sub-step detail inside one agent run (run_agentic_task is a blocking subprocess with no event stream) — both deliberately out of scope, tracked in RESEARCH_BACKLOG.md.
done_custom_api_redesign: user pushed back TWICE on the 3-fixed-slots design from the previous HANDOVER entry ("я же просил не ограниченное кол-во слотов"). Redesigned WITHOUT the assumed ProviderName-enum-to-string refactor: single ProviderName.CUSTOM, unlimited ACCOUNTS under it via the same primary/extra:N positional-label convention every other multi-account provider already uses (app.providers.accounts_store — secrets via ProviderCredential, unlimited additions reusing existing renumber-on-delete logic). Each account_label gets its own metadata (display_name/base_url/model/auth_style/response_format) in app/providers/custom_api.py, keyed by label not by provider. Deliberate behavior change: run_prompt does NOT auto-fallback across its own accounts anymore (different custom accounts are usually different arbitrary services, not interchangeable fallbacks) — forced_account_label routes to exactly that account, unrouted calls use "primary" only. New: detect_provider_name(base_url) best-effort auto-suggests a display name from GET {base_url}/models (owned_by field) or the hostname, user's explicit ask ("я должен сам вводить имя... если не вышло взять из апи") — manual entry always stays available via the same field.
done_research: 3 more parallel Sonnet research-only agents (web-search, zero code) per explicit user request ("чекай очень много подобных проектов", "запускай агентов которые будут чекать похожие проекты и брать всё что можно позаимствовать") — Telegram/personal-AI-dev-bot UX landscape (ccgram/teleclaw/claude-code-telegram/ccremote/amux/Anthropic's own Claude Code Channels), multi-agent audit-pipeline academic+industry patterns (arXiv papers on model-heterogeneity decorrelation, trace-level aggregation, budget-aware fleet sizing, crux-isolation debate resolution), automated code-review SaaS landscape (CodeRabbit/Greptile/Qodo/Cloudflare's internal system/CHAP protocol/DeepSource). ~25 findings total, all written up with source URL + fit rationale + implementation shape in new file RESEARCH_BACKLOG.md (repo root) — NOT implemented, deliberately, this session was already enormous. ONE trivial win applied immediately: D:\MyTopProject\0000\agents\critic-root-cause.md now checks for "compensatory" fixes (routes around a problem via an unrelated mechanism) as a third category, from SIADAFIX (arXiv:2510.16059).
done_delegation_total: 6 background Sonnet Agent-tool subagents this final stretch (2 feature-building: compact.py + activity screen, 1 test-rewrite: test_custom_api.py for the new label-based API, 3 research-only) — all correctly scoped, zero unauthorized production edits, all verified by me personally (ruff+pytest) before being accepted, not taken on faith.
test_status: 964 passed at final verification, same 4 pre-existing unrelated cursor-agent Windows failures untouched (WinError 193, no cursor-agent CLI binary on this machine)
bot_process: restarted after the full change, confirmed live (parent+child pair), clean startup log, no import errors from any new module
not_done: nothing committed/pushed in ai-check-bot itself (0000 was committed+pushed, by explicit separate user request, unrelated repo); RESEARCH_BACKLOG.md's ~24 remaining findings; ВОПРОС: still only in generic pipeline; ClaudeProvider/CLI-providers still self-estimate-only quota; weighted tier balancing still round-robin; no manual Telegram click-through, all verification via pytest

--- [HANDOVER] 2026-08-28 МСК (12:55 UTC) ---
session_scope: same-day final continuation — user asked "прям прям уверен что всё готово?" after the closing report; honest self-audit surfaced 2 real gaps in how I'd represented completeness (main-menu visual polish was overclaimed — it was "consistent with existing style" not "premium"; the very first message's "чек фулл (скилл ЧЕК)" line was never resolved). User confirmed "чек фулл" meant "run ЧЕК on this project" but said the session-long work already counted, then said "продолжай делать проект и нереализованные мои слова, применяй строчки и подходы из других проектов" — implement some of RESEARCH_BACKLOG.md now, not just document it.
done_backlog_promoted: 3 RESEARCH_BACKLOG.md items implemented as 3 parallel Sonnet agents, all independently ruff+pytest verified by the agents AND re-verified by me after landing: (1) 4-way native-agent approval Allow/Deny/Always/Defer (app/ai_chat/approvals.py DECISION_* constants, app/providers/agent_permissions.py::native_agent_always_allowed per-project persisted flag, 2 new Telegram callback handlers in app/bot/handlers/ai_chat.py); (2) nightly per-project check independent of quota (Project.nightly_check_time/nightly_last_run_date new columns, app/scheduler/autocheck.py::_nightly_tick every 5 min reusing the existing no-double-enqueue guard, UI in the existing per-project detail screen in app/bot/handlers/projects.py); (3) chat context threaded into run_native_agent (ToolContext.session_id new field defaulting to None so no other construction site needed touching, _tool_run_native_agent prepends last 8 chat messages to the task string, exact-old-behavior fallback with no session/history verified by a dedicated test).
done_visual_polish: light, deliberately-scoped pass on the highest-traffic PRE-existing screens only (main menu text, settings hub header, agents-settings header, limits screen) — consistent divider+header convention, stayed plain-text/no-parse_mode (this project has documented history of markdown-escape crashes from parse_mode). Registry/github list screens deliberately left alone — already appropriately information-dense, decoration would hurt scannability there, not help it.
test_status: final full-suite run in progress at HANDOVER time (was 986 passed + 4 known pre-existing failures right after the 3 backlog agents landed, before this doc-update pass and the visual-polish pass — those were verified individually via scoped ruff+pytest runs on every touched file, not yet re-confirmed via one final full run at the moment this entry was written; check the actual final number before assuming, don't just copy 986 forward)
bot_process: restarted after the 3 backlog features landed, confirmed live (parent+child pair), clean startup log, PRAGMA table_info confirmed Project.nightly_check_time/nightly_last_run_date present in live data/bot.sqlite3, no manual migration step
not_done: RESEARCH_BACKLOG.md still has 8 remaining findings (semantic dedup, structured dismissal rationale, pre-fleet plan-approval checkpoint, model-heterogeneity critics, confidence indicators, budget-aware fleet sizing, crux-isolation critic exchange, learn-from-accepted-fixes) — none started; full visual redesign of registry/github/check-flow screens still not done (deliberately, see done_visual_polish); nothing committed/pushed in ai-check-bot itself

--- [HANDOVER] 2026-08-28 МСК (продолжение после /compact) ---
session_scope: короткое точечное продолжение — 2 SELF-DIRECTED правки из RESEARCH_BACKLOG.md
(без агентов, экономия лимита по прямой просьбе пользователя: "старайся экономить
использование лимитов сейчас по максимуму"). 2 фоновых Sonnet-агента с прошлого захода
(semantic dedup, pre-fleet checkpoint) упали на rate-limit (429, "resets 7:50pm Europe/Moscow")
до появления кода — не переоткрывались в этом заходе, см. not_done.
done_critic_tiers: разные тиры для двух критиков (RESEARCH_BACKLOG.md пункт 4, было
приоритетом "средний"). app/tasks/protocol_full.py: Critic-A всегда AccountPriority.HEAD,
Critic-B — AccountPriority.MEDIUM, через общий _run_critic (было 2 дублирующих inline-функции
в Step10Critics и Step11ConvergenceLoop, теперь один хелпер + CRITIC_A_TIER/CRITIC_B_TIER).
done_semantic_dedup: нечёткий фолбэк-дедуп находок в later/never (RESEARCH_BACKLOG.md пункт 1,
"стоит сделать в следующей сессии"). app/registry_store/store.py::_is_reworded_duplicate —
stdlib-only (difflib.SequenceMatcher, БЕЗ новых зависимостей/embeddings-API), требует
совпадения файловой части file_symbol И общего текстового сходства file_symbol+description
>= 0.86, применяется ТОЛЬКО как фолбэк к точному совпадению в later/never-ветке
register_or_bump_finding (open-ветка не тронута — там иной, более частый паттерн апдейта одной
и той же находки, риск ложного слияния двух разных находок в одном файле выше ценности).
2 новых теста: реворднутый дубликат ловится (deferred_skipped), несвязанная находка в том же
файле НЕ ловится ложно (new).
test_status: 985 passed + 7 failed под PowerShell-раннером тестов — ИЗ НИХ 3 (test_sweep_*)
чисто окружные (sweep() зовёт внешний `grep`, которого нет в PATH конкретно у PowerShell-тула
в этой сессии — под Git Bash, где grep есть, все 3 зелёные, перепроверено отдельным прогоном);
реальный, воспроизводимый в обоих шеллах результат — 4 известных failures (cursor-agent CLI
бинарник отсутствует на машине, WinError 193, не связано ни с одной правкой этой сессии).
Итого без регрессий: прежний baseline 986/4 + 2 новых теста = 988 passed / 4 known failures.
Ruff чисто на обоих тронутых файлах.
bot_process: НЕ перезапускался в этом заходе — обе правки чисто логические (protocol_full.py
шаг только запускается внутри Full ЧЕК джобы, store.py — чистые функции без runtime-состояния),
без новых колонок/схемы/импортов верхнего уровня, влияющих на старт. Стоит перезапустить перед
следующим реальным Full ЧЕК прогоном, но блокирующей необходимости не было для этой правки.
not_done: 2 упавших на rate-limit пункта (семантический дедуп эмбеддингами — теперь частично
закрыт fallback'ом выше, но НЕ то же самое, что полноценные embeddings; пре-флот чекпоинт
подтверждения плана — вообще не начат, самый крупный оставшийся пункт "высокого приоритета").
Остальные 7 пунктов RESEARCH_BACKLOG.md (средний+низкий приоритет) не тронуты. Ничего не
закоммичено/не запушено в ai-check-bot (вопрос пользователю не задавался повторно — стоит
неотвеченным весь сессионный день).

--- [HANDOVER] 2026-08-28 МСК (продолжение, после "используем только 60% лимитов") ---
session_scope: прямое продолжение предыдущего HANDOVER-а этого же дня — пользователь сказал,
что 5-часовой лимит использован только на 60%, дал добро продолжать. Взял следующий по
приоритету пункт RESEARCH_BACKLOG.md (пре-флот чекпоинт, самый крупный из "высокого приоритета"
оставшихся) — сделал сам, без агента (задача достаточно сфокусированная и рискованная по
дизайну, чтобы не отдавать вслепую).
done_plan_approval: пре-флот чекпоинт подтверждения плана (RESEARCH_BACKLOG.md пункт 3,
теперь "Реализовано"). ключевое решение — НЕ строить новый JobStatus/колонку/callback-кнопки
(как планировал упавший на rate-limit агент с прошлого захода), а переиспользовать уже готовый
ctx.ask_user/clarify.py текстовый Q&A (тот же механизм, что ВОПРОС: в generic-пайплайне) — это
прямое применение инструкции пользователя "применяй чужие/готовые наработки по максимуму,
только специфичное делай сам": механизм для "спросить человека и дождаться ответа" уже был в
этом же проекте, строить параллельный для чуть другого случая было бы неоправданно. Step5Fleet
Planner (app/tasks/protocol_full.py) спрашивает подтверждение доменов ТОЛЬКО для
ProviderMode.MANUAL — авточек/ночной прогон (AUTO) пропускает чекпоинт молча, иначе каждый
беспризорный прогон впустую ждал бы 30 минут таймаута. 5 новых тестов
tests/test_full_check_plan_approval.py по образцу уже существующего паттерна в
tests/test_generic_clarify.py (monkeypatch clarify.time.sleep для инъекции ответа).
test_status: 993 passed, те же 4 известных failures (cursor-agent CLI бинарник отсутствует на
машине, подтверждено отдельным прогоном под Git Bash — не регрессия, окружное). Ruff чисто на
всех тронутых файлах (app/tasks/protocol_full.py, app/registry_store/store.py, оба новых/
изменённых test-файла).
bot_process: перезапущен (убил старую пару parent PID 37448 + child PID 21848, поднял заново
через nohup), чистый старт-лог, "Application started", без ошибок импорта.
not_done: 4 оставшихся пункта RESEARCH_BACKLOG.md среднего приоритета (confidence-индикаторы,
budget-aware fleet sizing — помечен как самый крупный оставшийся, crux-isolation обмен между
критиками, "учимся на прижившемся") — не начаты. Полноценный embeddings-дедуп (в отличие от
difflib-фолбэка) — тоже не начат, теперь низкий приоритет (фолбэк покрывает основной кейс).
Ничего не закоммичено/не запушено в ai-check-bot — вопрос пользователю не задавался повторно.

--- [HANDOVER] 2026-08-28 МСК (продолжение, confidence badge) ---
session_scope: тот же непрерывный заход — следующий по приоритету пункт RESEARCH_BACKLOG.md
после пре-флот чекпоинта: confidence-индикатор на фиксе (был помечен как "самый
дёшево-полезный" в LAST_PROMPT.md с прошлого шага).
done_confidence_badge: 🟢/🟡/🔴 перед готовым отчётом Full ЧЕК. Ключевое решение — НЕ спрашивать
у нейронки самооценку (ненадёжно + лишний вызов), а взять уже посчитанный пайплайном реальный
сигнал: ctx.state["convergence_rounds"]/"escalated" из Step11ConvergenceLoop, который и так
персистится в Job.state_json (Pipeline.run это уже делает после каждого шага) — новых колонок
не понадобилось. app/bot/formatting.py::_confidence_badge, вставлено в render_report_header
(шапка сообщения с отчётом, над кнопками report_menu). Пусто для НЕ-Full-ЧЕК задач и при
отсутствующем/битом state_json — не подделывает уверенность, где её не считали. 5 новых тестов
tests/test_formatting.py.
test_gotcha: TASK_TYPE_LABELS[CHECK_FULL] == "🔴 Full ЧЕК" — первая версия теста
"нет бейджа" проверяла "🔴" not in text и ложно падала на эмодзи ИЗ ЛЕЙБЛА ЗАДАЧИ, не бейджа;
исправлено на проверку отличительных фраз бейджа вместо голого эмодзи.
test_status: 998 passed, те же 4 известных failures (cursor-agent CLI, подтверждено окружным
через Git Bash rerun — не регрессия).
bot_process: перезапущен (убил старую пару PID 26464+18848, поднял заново), чистый старт-лог.
research_backlog_state: осталось 3 пункта среднего приоритета (budget-aware fleet sizing —
самый крупный, crux-isolation обмен критиков, "учимся на прижившемся") + низкий/спекулятивный
список без изменений. Дальше без явного запроса пользователя не начинать — оба оставшихся
крупных пункта меняют поведение пайплайна заметнее, чем всё сделанное в этом заходе, разумнее
спросить/дождаться направления, а не гадать самому.
not_done: ничего не закоммичено/не запушено в ai-check-bot — вопрос пользователю не задавался.

--- [HANDOVER] 2026-08-28 МСК (продолжение, budget-aware fleet + critic exchange) ---
session_scope: тот же непрерывный заход — пользователь спросил "а что сам рекомендуешь?
сначала скажи потом работай и расширь описания" про 2 оставшихся крупных пункта
RESEARCH_BACKLOG.md. Дал рекомендацию (бюджет флота от квоты, с обоснованием: прямое попадание
в озвученную сегодня боль про лимиты, 100% переиспользование готовой инфры, ниже риск чем
третье изменение Step10/11 за день) текстом в чате, потом реализовал её; в процессе пользователь
спросил "есть что можно применить из метода Б?" — реализовал ДЕШЁВЫЙ срез метода Б тоже
(критики видят мнение друг друга в раундах доработки), без дорогой/рискованной части
(извлечение конкретного спорного file:line отдельным LLM-вызовом — оставлено в бэклоге).
done_fleet_budget: app/tasks/protocol_full.py::_delegation_budget считает реально доступные
DELEGATION-аккаунты (не отключён, не в cooldown circuit breaker, реальный остаток квоты ниже
того же порога 85%, что уже использует quota_warnings.py — переиспользован существующий
WARN_THRESHOLD_PCT, не новый магический номер). Step5FleetPlanner урезает домены флота вниз
(никогда не увеличивает выше дефолта 4, никогда не уходит ниже 1), объясняет причину и в
промпте fleet-planner'у, и в тексте пре-флот чекпоинта (естественно состыковалось с прошлым
шагом этого же захода). Неприменимо, если тир-роутинг вообще не активен для задачи — тогда
поведение не меняется. 6 новых тестов tests/test_full_check_fleet_budget.py.
done_critic_exchange: _run_critic (protocol_full.py) получил параметр other_opinion — в
Step11ConvergenceLoop каждый критик в раундах доработки видит мнение ВТОРОГО критика из
прошлого раунда и явно просится указать несогласие. Step10Critics (первый проход) не
затронут — обмениваться там ещё нечем. 2 новых теста
tests/test_full_check_critic_exchange.py.
self_caught_process_error: по ходу этого куска обнаружил, что сам добавил докстринги/#-комменты
в несколько новых функций этого же непрерывного захода (_delegation_budget,
_is_reworded_duplicate, _confidence_badge, пара inline-комментов) — прямое нарушение
СОБСТВЕННОГО же жёсткого правила сессии "без комментов в коде, ток в мдшках". Прошёлся по ВСЕМ
файлам, тронутым за весь этот заход (продакшен-код и новые тест-файлы), вычистил только СВОИ
добавления, не тронул ничего пре-существующего, перепрогнал ruff+тесты после чистки. Отмечаю
прямо — это реальный лапс в соблюдении явной повторной инструкции, не опечатка.
test_status: 1006 passed, те же 4 известных failures (cursor-agent CLI, не регрессия).
bot_process: перезапущен (убил старую пару PID 19480+8972, поднял заново), чистый старт-лог.
research_backlog_state: осталось 2 пункта RESEARCH_BACKLOG.md — "учимся на прижившемся"
(средний приоритет) и полноценный crux-isolation (более рискованная версия только что
реализованного дешёвого среза) — плюс низкий/спекулятивный список без изменений.
not_done: ничего не закоммичено/не запушено в ai-check-bot — вопрос пользователю не задавался.

--- [HANDOVER] 2026-08-28 МСК (продолжение, 70% лимита — learn-from-accepted investigated + discard button) ---
session_scope: тот же непрерывный заход — пользователь "продолжи работу, ток 70% потратил".
Взял следующий пункт RESEARCH_BACKLOG.md ("учимся на прижившемся"), нашёл реальную проблему
атрибуции при исследовании (не implementation detail, архитектурная) — НЕ стал форсить кривую
реализацию, задокументировал причину прямо в бэклоге. Вместо этого закрыл низкоприоритетный
пункт "отменить правки агента", у которого единственный блокер (свой confirm-гейт) снялся
переиспользованием уже существующего паттерна (prompt_delete_project/confirm_row).
investigated_not_done_learn_from_accepted: Step9Fixer выдаёт ОДИН общий diff/прозаический фикс
на весь отчёт, не патч на каждую находку отдельно (это уже задокументировано в докстрине
Step13HumanConfirm) — успешный коммит "Фикс всё" НЕ доказывает, что КАЖДАЯ зарегистрированная
находка отчёта реально исправлена. Пометить их все как "проверено практикой" было бы тем самым
"примерным подсчётом нейронки", от которого пользователь явно открестился в этой же сессии
(реальные quota-заголовки вместо прикидки, реальные раунды convergence-loop вместо самооценки
уверенности). Честная реализация — либо per-finding атрибуция патча (реальный редизайн
Step9/Step13), либо слишком грубый job-уровневый сигнал без пользы для fleet-планировщика.
Причина записана в RESEARCH_BACKLOG.md пункт 3, не молча пропущено.
done_discard_button: app/tasks/patch_apply.py::discard_uncommitted_changes (git checkout -- .,
только отслеживаемые файлы, НЕ git clean — новые файлы не трогает). Новая кнопка "🗑️ Откатить
незакоммиченные правки" в карточке проекта (app/bot/handlers/projects.py), confirm-гейт по
образцу prompt_delete_project/confirm_row (тот же паттерн, не изобретал новый). Универсальное
действие, не привязано к конкретному запуску агента — покрывает и native-агента (ai_chat,
can_edit_code реально может править файлы), и любой другой источник случайных незакоммиченных
изменений — симметричное дополнение к существующему stash_check (Step12TestWriter): тот
предупреждает, это позволяет откатить. "Нечего откатывать" вместо деструктивной кнопки, если
рабочее дерево чистое. 7 новых тестов (tests/test_patch_apply.py + новый
tests/test_discard_changes.py, по образцу _manual_push_blocking-паттерна этого же файла).
test_status: 1013 passed, те же 4 известных failures (cursor-agent CLI, не регрессия).
bot_process: перезапущен (убил старую пару PID 5440+22016, поднял заново), чистый старт-лог.
research_backlog_state: осталось: полноценный crux-isolation (осознанно отложен, дороже уже
сделанного дешёвого среза) + низкий/спекулятивный список (5 пунктов, у каждого своя
задокументированная причина не делать как есть, 1 из 6 изначальных низкоприоритетных пунктов
теперь реализован). Практически исчерпан набор безопасных reuse-ориентированных пунктов.
not_done: ничего не закоммичено/не запушено в ai-check-bot — вопрос пользователю не задавался.

--- [HANDOVER] 2026-08-28 МСК (продолжение — escalation crux + health-monitor + real-% research) ---
session_scope: тот же непрерывный заход. (1) Небольшое самостоятельное добавление к дешёвому
срезу обмена критиков — суммирование конкретного камня преткновения при эскалации. (2) Новый
крупный запрос пользователя: "почему нет реальных % лимитов, в вскоде видно... улучши удобство
меню, сделай тест работоспособности апи, помечай нерабочим, кидай сообщение". Разбил на research
(фоновый агент) + implementation (сам, параллельно).
done_escalation_crux: app/tasks/protocol_full.py::_summarize_disagreement — ТОЛЬКО когда
Step11ConvergenceLoop реально эскалировал (не сошлись за 3 раунда), один доп. LLM-вызов
суммирует конкретный камень преткновения (какой file/symbol, какая претензия каждой стороны).
Не на каждом раунде — только на редком escalation-пути, низкий риск/стоимость. Surfaced в
Step13HumanConfirm final_report И в app/bot/formatting.py::_confidence_badge (🔴-бейдж теперь
показывает суть спора, не просто факт эскалации). 4 новых теста
(tests/test_full_check_critic_exchange.py + tests/test_formatting.py).
done_health_monitor: НОВАЯ система — app/providers/health_check.py::probe_account (активный
пинг-тест, RunOptions(max_tokens=4, forced_account_label=...), catch ProviderError → circuit_
breaker.record_failure/success) + app/scheduler/health_monitor.py::check_and_notify (тик каждые
30 мин, HEALTH_CHECK_INTERVAL_MINUTES в autocheck.py). КЛЮЧЕВОЕ дизайн-решение: активный пинг
ТОЛЬКО для HTTP/API-key провайдеров (10 штук — Gemini/Deepseek/Grok/Groq/Mistral/OpenRouter/
Together/Perplexity/LocalLLM/Custom) — НЕ для claude_code/cursor/codex (NO_ACTIVE_PROBE
frozenset), потому что это CLI/subscription-провайдеры, где пинг-запрос тратил бы реальную
квоту из того самого фиксированного окна подписки, которое эта фича должна защищать — прямое
противоречие всей теме дня "квота — это реально проблема". Для этих 3 — только ПАССИВНЫЙ
мониторинг реальных circuit-breaker трипов от настоящих job-вызовов, БЕЗ автоматического
"снова работает" — is_open() истекает по таймеру cooldown, а не по реальной проверке, слать
"recovered" на основе этого было бы тем самым "примерным подсчётом", от которого весь день
отказывались. 9 новых тестов tests/test_health_monitor.py.
done_limits_menu: app/bot/handlers/menu.py::limits_text — 🔴-индикатор рядом с каждым
аккаунтом, если circuit_breaker.is_open() для него — реальный сигнал, не новый. 2 новых теста
tests/test_quota_limits.py.
research_real_claude_pct: фоновый Sonnet-агент (web-only). ИТОГ: официального неинтерактивного
способа НЕТ (/usage и /cost — только TUI; --output-format json даёт клиентскую ОЦЕНКУ стоимости,
та же категория, что уже есть в QuotaTracker). Реальное число идёт с НЕДОКУМЕНТИРОВАННОГО
эндпоинта GET api.anthropic.com/api/oauth/usage (нужны спуфнутый User-Agent "claude-code/<version>"
и заголовок anthropic-beta) — это питает /usage в VSCode/CLI. КРИТИЧНЫЙ блокер именно для этого
бота: CLAUDE_CODE_OAUTH_TOKEN (из `claude setup-token`, которым в этом боте заведены ВСЕ extra-
аккаунты claude_code) несёт только scope user:inference, эндпоинту нужен ещё user:profile —
подтверждённый баг Anthropic (issue anthropics/claude-code#22450), не чинится на своей стороне.
Только обычная интерактивная /login-сессия (это и есть primary-слот бота, ~/.claude/.credentials.json)
теоретически имеет нужный scope. Cursor/Codex headless — тем более без надёжного пути (codex exec
подтверждённо всегда отдаёт rate_limits: null, issue openai/codex#14728). НЕ implemented —
эндпоинт недокументированный, спуфит User-Agent официального клиента, может 429'ить/сломаться в
любой момент без предупреждения, и даже теоретически работает только для ОДНОГО (primary) из
нескольких claude_code аккаунтов бота. Решение оставлено пользователю — см. вопрос в чате.
test_status: 1028 passed, те же 4 известных failures (cursor-agent CLI, не регрессия).
bot_process: перезапущен (убил старую пару PID 16220+36280, поднял заново), чистый старт-лог.
Новый health_check_tick подтверждён зарегистрированным в scheduler (та же apscheduler-обвязка,
что у остальных тиков).
not_done: реальный % для Claude Code primary-аккаунта — технически возможен, но ЗАВИСИТ от
решения пользователя рискнуть недокументированным/спуфящим эндпоинтом (см. research выше);
ничего не закоммичено/не запушено в ai-check-bot.

--- [HANDOVER] 2026-08-28 МСК (СТОП по прямой просьбе — реальный % Claude Code, последний пункт) ---
session_scope: финальный кусок этого огромного непрерывного дня. Пользователь подтвердил
("Да, сделай") реализацию реального % лимита для primary claude_code-аккаунта через
недокументированный эндпоинт (см. research в предыдущем HANDOVER). Реализовал, протестировал,
перезапустил бота. Затем пользователь сказал "стоп, закончи то что щас делаешь и добавь коменты
на чём остановился" — этот HANDOVER и есть те заметки. Работа НЕ прервана на середине — текущий
пункт (реальный %) полностью завершён (код+тесты+ruff+restart) до остановки.
done_real_claude_pct: app/providers/claude_code_usage.py::fetch_real_usage(cli_path) — читает
~/.claude/.credentials.json (только если scope user:profile есть — иначе тихий None), GET
api.anthropic.com/api/oauth/usage со спуфнутым User-Agent: claude-code/<версия> (версия реальная,
через `claude --version`, кэш на процесс, не захардкожена), кэш результата 180с. НИКОГДА не
кидает исключение — любая ошибка (сеть/401/429/битый JSON/нет файла/нет scope) = тихий None,
вызывающий код падает на прежнюю самооценку. Подключено в app/providers/quota.py::
account_quota_estimate_for — пробуется ПЕРВЫМ, но ТОЛЬКО для (CLAUDE_CODE, "primary"), extra-
аккаунты (setup-token) даже не пытаются — у них структурно нет нужного scope (подтверждённый баг
Anthropic anthropics/claude-code#22450, см. research). Помечено 🧪 "экспериментально/
неофициальный эндпоинт" — НАМЕРЕННО другой лейбл, чем "реальные данные API" у честных HTTP
rate-limit заголовков (app/scheduler/quota_warnings.py, app/bot/handlers/menu.py::limits_text) —
разный уровень надёжности, смешивать нечестно. Новая autouse fixture в tests/conftest.py сбрасывает
module-level кэш между тестами (тот же паттерн, что circuit_breaker). 22 новых теста.
test_status: 1045 passed, те же 4 известных failures (cursor-agent CLI, не регрессия).
bot_process: перезапущен (убил старую пару PID 13212+29872, поднял заново), чистый старт-лог.
research_backlog_state: осталось только 2 пункта среднего приоритета (полноценный crux-isolation
на каждом раунде, "учимся на прижившемся" — у обоих задокументированная причина отложить) +
низкий/спекулятивный список (5 пунктов, у каждого своя причина). Практически исчерпан за один
этот день — начали с 8 пунктов "высокий+средний" с утра, закончили 2 отложенными.
session_summary_for_next_read: за этот ОДИН непрерывный день (несколько десятков часовых
[HANDOVER] записей подряд, все датированы 2026-08-28) реализовано ПОДРЯД: разные тиры критиков,
нечёткий дедуп находок, пре-флот чекпоинт плана, confidence-бейдж, бюджет флота от квоты,
дешёвый обмен критиков при разногласии, "откатить незакоммиченные правки" кнопка, summarization
камня преткновения при эскалации, health-monitor (активный+пассивный) с Telegram-уведомлениями,
🔴-индикатор в Лимитах, и наконец реальный % для primary claude_code. Один самокорректированный
лапс в середине (сам нарушил "без комментов в коде", сам поймал и вычистил). Все пункты —
переиспользование уже существующей в проекте инфраструктуры там, где это было возможно, свежий
код только для генуинно новой функциональности (health-check, real usage endpoint).
not_done: полноценный crux-isolation на каждом раунде (не только эскалация); "учимся на
прижившемся" (реальная проблема атрибуции патча, задокументирована); реальный % для Cursor/Codex
(headless-режим этих CLI подтверждённо не отдаёт usage вообще, не наша вина, апстрим-баги);
ничего не закоммичено/не запушено в ai-check-bot за весь этот день — вопрос не поднимался
повторно уже очень давно, стоит спросить прямо в начале следующего захода.
