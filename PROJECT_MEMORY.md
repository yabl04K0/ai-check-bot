# PROJECT_MEMORY — ai-check-bot structure, patterns, invariants, history (LLM-ONLY, English on purpose)

PURPOSE: carry context between sessions. ALWAYS read this at session start (sections above SESSION LOG only).
  Updated by the AI as work proceeds; committed together with the code when the user asks to commit.
FORMAT: flat text, `key: value`, no tables, no decoration.
RELATED: CLAUDE.md rules · AI_COMMANDS.md triggers · CHEK_PROTOCOL.md · BRANCHING.md · STATE_LOG.md ·
  TROUBLESHOOTING.md · chek_*.md · README.md (current product spec) · .claude/commands/

# ============================================================================
# AI OPS STACK (ported from the sibling projects — logic, not domain)
# ============================================================================

purpose: same operating system for AI work as yabl04k0/MeCelium and yabl04k0/AutoPost — triggers, CHEK fleet,
  handover, try-and-log, branching gates, registries. Domain differs (dev-task dispatcher vs VPN shop / auto-poster);
  the WORKFLOW logic must match.
ported_and_adapted:
  CLAUDE.md — full rules, adapted: AI-kit sync model added (this repo is the first to receive it), provider
    abstraction and repo-deletion-unreachable rules added (specific to this bot's product), Windows/PowerShell
    specifics removed.
  AI_COMMANDS.md — same triggers (PROMPT_RUN/SHOW/WRITE, CHEK, CHEK_REVIEW, HANDOVER, DM_USER) plus AI_KIT_SYNC.
    DM_USER marked NOT YET WIRED until the Telegram layer exists.
  CHEK_PROTOCOL.md — steps 1-13, kept close to verbatim (protocol is designed portable); project-specific reference
    examples (domain splits, footgun patterns) genericized since this repo has no code yet.
  DELEGATION.md — REWRITTEN, not ported: sibling projects wire a Cursor Agent CLI fleet via PowerShell; this repo
    runs in Claude Code, so delegation is the native Agent tool directly, no external CLI.
  BRANCHING.md — main/beta/alpha + GATE-CONFIDENT + GATE-SOAK, paths and soak examples genericized (no deploy
    target yet).
  TOKEN_ECONOMY.md — in-session model routing (top model decides, mid-tier builds/reviews, cheap searches/runs/
    logs), read budget, `.claude/settings.json` rationale.
  STATE_LOG.md / TROUBLESHOOTING.md / LAST_PROMPT.md / chek_open.md / chek_later.md / chek_never.md — skeletons,
    empty of domain history (this repo has none yet).
  .claude/commands/prompt.md + chek.md — slash stubs pointing at AI_COMMANDS.md.
  .claude/agents/*.md — scout · runner · scribe (haiku) · builder · reviewer (sonnet), same roles as siblings.
  agents/web-researcher.md — CHEK Step 4b role, already project-agnostic, ported near-verbatim.
NEW_IN_THIS_REPO (not present in the siblings):
  tools/ai_kit.json — the sync manifest: which files are TIER A (synced from the private AI-kit structure repo,
    structure repo authoritative unless a `local-override` marker says otherwise) vs TIER B (local-only: this file,
    STATE_LOG, LAST_PROMPT, TROUBLESHOOTING, chek_open/never/later, README, docs/).
  AI_KIT_SYNC command (AI_COMMANDS.md) — replaces the siblings' `tools/sync_ai_kit.ps1` with a mechanism-agnostic
    trigger; the actual sync tool lives in the structure repo, not duplicated here.
handover_rule: AI_COMMANDS HANDOVER + CLAUDE.md CRITICAL session handover — mandatory when the user switches chats.
session_start: PROJECT_MEMORY.md -> AI_COMMANDS.md -> latest STATE_LOG [HANDOVER] if present -> chek_open.md.

# ============================================================================
# PROJECT STRUCTURE
# ============================================================================

path: repository root (GitHub: yabl04k0/ai-check-bot, public)
status: scaffold only. README.md is the current spec/source of truth; no bot code exists yet (see README "Статус").
stack (planned, from README "Стек"): Python, python-telegram-bot, apscheduler, sqlalchemy, PyGithub, httpx.
  Provider clients: anthropic SDK (Claude), OpenAI SDK / Codex CLI wrapper (Codex), Cursor Agent CLI wrapper
  (Cursor), OpenAI-compatible client against a local endpoint (Ollama/vLLM for a local model).
architecture docs: docs/architecture/ui-map.mermaid (bot menu map), docs/architecture/backend-architecture.mermaid
  (backend) — both describe the Claude-only, Chek-only earlier design; not yet updated for multi-provider / the
  Feature-Fix-Refactor-Custom task types.
core abstraction: `AIProvider` interface (src/ai_check_bot/providers/base.py) — every provider-specific call must
  route through it; see CLAUDE.md "CRITICAL: provider abstraction". Implemented so far: `ClaudeProvider`
  (providers/claude.py, wraps anthropic.AsyncAnthropic, per-account proxy_url via a custom httpx.AsyncClient).
  Codex/Cursor/local-LLM providers are NOT implemented yet — `providers/registry.py` PROVIDER_REGISTRY has one
  entry ("claude"); adding a provider means one new module + one registry line, nothing else changes.
api-account health probe (README's "просто чтобы проверять API фишки" ask): implemented as
  `src/ai_check_bot/probe_service.py` + `scheduler.py`. Model: AIAccount (one credential + optional per-account
  proxy_url) has up to `config.MAX_PROBES_PER_DAY` (5) ProbeSchedule rows (HH:MM UTC + a configurable probe
  message); APScheduler (UTC-pinned, see scheduler.py comment on why) fires `run_probe` at each time, which calls
  `AIProvider.probe()` and logs a ProbeRun row (success/latency/error). "Open a new chat, get a response, delete
  the chat" from the original ask maps to `AIProvider.probe()`'s contract: exchange one round trip and clean up any
  server-side conversation resource — for Claude's stateless Messages API that cleanup is correctly a no-op (see
  providers/claude.py docstring); a future stateful provider (e.g. an Assistants-style thread API) would actually
  create+delete there.
bot UI: `src/ai_check_bot/bot.py` is admin-only text commands (`/add_account`, `/add_schedule`, `/accounts`,
  `/probe_now`), deliberately NOT the inline-keyboard menu README describes — that needs the UX pass over
  MeCelium/AutoPost/sd-forge-bot (see LAST_PROMPT.md backlog item 6) first, or it gets built twice.
NOT implemented yet (see LAST_PROMPT.md for the full backlog and priority order): live in-flight agent-status menu,
  mid-task chat/stop, multi-account POOLING/routing logic (multiple AIAccount rows per provider already work as
  storage, but nothing picks between them yet), the polished inline-keyboard menu, Codex/Cursor/local-LLM
  providers, encrypted credential storage (AIAccount.api_key is plaintext in SQLite for now — flagged inline).
invariants stated in README, not yet enforced by code (enforce when the corresponding module is built):
  SHOP-equivalent for this project: none (not a paid-tariff product).
  self-check (this repo) never auto-pushes even when auto-push is enabled for other managed projects.
  repo-delete / history-rewrite must be structurally unreachable through any code path (bot handler, AIProvider
    method, GitHub module) — not just denied in the UI.
  human confirms every commit/push regardless of which provider executed the task.

# ============================================================================
# SESSION LOG (history — read only when asked about history)
# ============================================================================

- 2026-08-20 — ported the AI-collaboration doc stack (CLAUDE.md, AGENTS.md, AI_COMMANDS.md, CHEK_PROTOCOL.md,
  DELEGATION.md, BRANCHING.md, TOKEN_ECONOMY.md, PROJECT_MEMORY.md, STATE_LOG.md, LAST_PROMPT.md,
  TROUBLESHOOTING.md, chek_open/never/later.md, .claude/commands/{prompt,chek}.md, .claude/agents/{scout,runner,
  scribe,builder,reviewer}.md, agents/web-researcher.md, .claude/settings.json, tools/ai_kit.json) from
  yabl04k0/MeCelium and yabl04k0/AutoPost, adapted for Claude Code on Linux (no Cursor CLI, no Windows paths) and
  for this bot's own multi-provider/no-code-yet state. Did NOT run a CHEK audit (no code to audit; explicitly
  deferred by the user). Added the AI-kit sync model (structure-repo-authoritative-with-override) as new content,
  not present in the sibling projects at time of porting.

- 2026-08-20 (same day, later) — created two sibling repos for the AI-kit: yabl04K0/0000 (private, canonical
  TIER A doc stack + tools/sync_ai_kit.py) and yabl04K0/1111 (public mirror of CHEK_PROTOCOL.md alone). Updated
  this repo's tools/ai_kit.json to point at them (the placeholder names ai-dev-kit/chek-protocol were not
  available; GitHub repo creation is not reachable from this session by any method — MCP tool lacks Administration
  permission, and the session's network proxy blocks all raw GitHub write calls outside the allowed MCP tools —
  the user created the two repos by hand instead). yabl04K0/1111 still needs its visibility flipped to public by
  hand; no path exists to do that from this environment either.

- 2026-08-20 (same day, later still) — implemented the first item of the feature backlog: `AIProvider`
  abstraction, `ClaudeProvider`, SQLAlchemy models (AIAccount/ProbeSchedule/ProbeRun), probe_service.py,
  APScheduler wiring, and a minimal admin-only bot.py. Critic pass caught and fixed 4 real bugs before tests ran:
  Base.metadata.create_all() silently creating zero tables if models.py was never imported first (db.py now
  force-imports it); APScheduler defaulting to local system timezone while the bot tells the user "UTC"
  (scheduler now pinned to timezone="UTC" explicitly); naive `datetime.utcnow()` on both timestamp columns
  (switched to timezone-aware `datetime.now(timezone.utc)` — this exact footgun is literally named in this
  project's own CHEK_PROTOCOL.md reference examples); and a bare `except Exception` in bot.py that would have
  swallowed real bugs, not just the intended IntegrityError. A 5th bug (the MAX_PROBES_PER_DAY=5 limit never
  actually triggering) was caught BY a test, not the critic pass: `account.schedules` is a relationship collection
  that caches on first access under `expire_on_commit=False`, and add_schedule() was writing new rows via the raw
  `account_id` column rather than the relationship attribute, so the cached collection never saw them — fixed by
  querying ProbeSchedule directly instead of trusting the relationship cache. 8/8 tests green after the fix
  (`pytest -q`). Committed as 988d115.

- 2026-08-20 (same day, later still) — implemented the rest of the feature backlog the user described, after
  reading the sibling bots' actual UI code for patterns (MeCelium's src/mecelium/bot/ui.py — OK-dismiss/edit-in-
  place pattern, ported to python-telegram-bot as ui.py; sd-forge-bot's keyboards.py — 2-column compact rows with
  the current value shown inside the button label, ported as keyboards.py, since it already uses the same
  `telegram` library this project does, unlike MeCelium/AutoPost's aiogram):
  - AIAccount gained `enabled` (router/scheduler skip disabled accounts) and proxy/enable/delete/lookup service
    functions (probe_service.set_account_proxy/set_account_enabled/delete_account/get_account_by_label).
    add_account() now rejects an unknown provider AT ADD TIME (UnknownProviderError) instead of failing every
    future probe against that account with a bare ValueError from get_provider().
  - providers/router.py: pick_account(session, provider) — least-recently-used-by-ProbeRun selection across every
    enabled account of a provider. This is the actual payoff of "multiple accounts, seamless" — task_service.py's
    run_custom_task() calls it so a user names a PROVIDER, never a specific account.
  - input_flow.py: waiting_for + waiting_for_set_at with a TTL, checked on read — the sibling bots' own CLAUDE.md
    flags exactly this pattern ("waiting_for without waiting_for_set_at, TTL dead, state stuck forever") as a
    known footgun; built it correctly from the start instead of reproducing it.
  - jobs.py: a generic in-flight job (live per-worker status text, a cancel flag for cooperative multi-worker
    loops, AND real asyncio.Task cancellation via attach_task/request_cancel for a single long call). This is the
    "which/how many agents active" + "stop mid-task" mechanics — infrastructure, not the CHEK fleet itself (that
    still does not exist; see NOT implemented below). Proven on two real call sites, not just unit tests:
    bot.py's "🔄 Проверить всё сейчас" (probe every enabled account, cooperative cancel between accounts) and
    "✨ Новая задача" (README Task Type "Кастом": one real AI call via run_custom_task, REAL mid-flight
    cancellation — tapping cancel calls asyncio.Task.cancel() on the actual in-flight anthropic call, not just a
    flag checked between steps, because there are no discrete steps in a single call).
  - bot.py rewritten from flat admin commands to the inline-keyboard menu (keyboards.py): 🔌 Провайдеры ИИ ->
    account list -> account detail (schedule / proxy / probe now / enable-disable / delete-with-confirm) ->
    schedule submenu (list/add/delete, still capped at 5). Free-text input after a button prompt (add account,
    set proxy, add schedule, task prompt) goes through input_flow's waiting_for; a message with no pending
    waiting_for gets queued as a job interjection if one is running for that chat, otherwise gets a hint to
    /start. The old flat commands (/add_account etc.) were removed rather than kept alongside the menu — two UIs
    for the same thing invites exactly the drift CLAUDE.md's own doc-writing rules warn about.
  - Critic pass + tests caught 3 more real bugs beyond the 5 from the previous entry: a duplicate `cancel_job_cb`
    definition (dead code from an editing mistake); an awkward local import "to avoid a cycle" that did not
    actually avoid any cycle (removed, pick_account now imported normally at module level); and — caught by a
    test, not the critic pass — router.pick_account comparing an offset-naive datetime (SQLite drops tzinfo on
    round-trip through sqlalchemy.DateTime even when the Python side used datetime.now(timezone.utc)) against an
    offset-aware sentinel, raising TypeError. Fixed by adopting one explicit convention project-wide
    (models.utc_now(): naive datetime, always UTC) instead of tz-aware datetimes that SQLite cannot actually
    preserve — this supersedes the "timezone-aware datetime.now(timezone.utc)" fix noted in the previous session-
    log entry; that fix was correct in isolation but incomplete once a second session read the value back.
  - 34/34 tests green (`pytest -q`): 8 from the previous entry + 26 new (jobs.py, router.py, input_flow.py,
    task_service.py, and the extended probe_service.py surface).

  NOT implemented, and each is a materially different scope of work than what's above, not a small remaining
  step: Codex/Cursor/local-LLM providers (only ClaudeProvider exists; adding one is one module + one registry
  line per providers/registry.py's own docstring, but nobody has written that module); persisted task/probe
  history beyond the live-during-the-job jobs.py state (README's "История задач" admin screen); encrypted
  credential storage (AIAccount.api_key is plaintext, flagged inline in models.py since the first entry); and —
  the big one — the actual 13-step CHEK audit fleet against an arbitrary target repo (README "Режимы аудита"),
  which means spawning/monitoring real external agent sessions per provider, GitHub read/write integration, and
  quota tracking. That is this bot's entire core product, not a backlog line item, and deserves its own properly
  scoped build rather than being rushed alongside everything above.

- 2026-08-21 — started on the CHEK fleet (LAST_PROMPT.md's GOAL) with the one slice that's genuinely tractable
  without an agent-loop engine: `chek_registry.py` implements CHEK_PROTOCOL.md Step 1 (load chek_open/never/
  later.md, check the "each id in at most one file" invariant) and the write-back half of Step 13 (append/remove
  an entry). Parses/renders the exact YAML-list-under-a-marker-comment format each registry file documents in its
  own header, via PyYAML (new dependency — justified per CLAUDE.md's minimal-code ladder: hand-rolling a parser
  for nested indented lists would be more code and more bug-prone than a standard, small, stable dependency).
  Deliberately does NOT implement: Step 1's GC-against-code check (needs Grep/Glob over a target repo — belongs
  to the orchestrator, not this pure-parsing module) or anything from Steps 4b-12 (the actual fleet — still not
  started; see LAST_PROMPT.md for why that's its own scoped effort).
  Tests caught one real gap during the critic pass (not a production bug, a coverage gap): the round-trip test
  for `passes_run=0` would have passed even if the serializer silently dropped zero values (re-parsing falls
  back to the dataclass default, which is also 0) — added a test that inspects the raw serialized YAML text
  directly, not just the value after re-parsing, to actually pin the `v not in (None, [], "")` filter (not
  `if v` truthiness) that makes zero survive.
  50/50 tests green (`pytest -q`). Committed as 848cd5b.

- 2026-08-23 — continued the CHEK fleet groundwork: `chek_scan.py` implements Step 2 (derive and run the target
  project's test command — pytest/npm/cargo/go, in that detection order; parses pytest's "N passed"/"N failed"
  summary specifically, other ecosystems get `ran=True` with `passed=failed=None` and the real output tail rather
  than a guessed/wrong parse) and Step 4 (grep sweep — pure Python re + rglob, no external grep/ripgrep dependency,
  so it behaves identically regardless of what's on the host running the bot). Tests for run_tests() spin up a
  REAL tiny pytest project in tmp_path and run it as a subprocess rather than mocking subprocess.run — genuine
  integration coverage of the parsing logic against real pytest output, not a guess at its format.
  Critic pass fixed two small things before they became real problems: an unused `field` import left over from an
  earlier draft, and `grep_sweep` computing `path.relative_to(project_path)` twice per file (once for the
  exclude-dir check, once for the hit record) — now computed once and reused.
  63/63 tests green. Still not started: anything from Steps 5-12 (fleet planner, checkers, fixer, critics — the
  actual agent-loop orchestration). Steps 1, 2, 4, and 13's write-back are now real; Steps 3 (deploy state), 4b
  (web research), and 5-12 remain. See LAST_PROMPT.md.

- 2026-08-23 (same day, later) — built the foundation Steps 5-12 actually need: a real tool-use agent loop, since
  ai-check-bot runs standalone (not inside Claude Code) and has no "Agent tool" to lean on — it has to implement
  its own minimal Read/Glob/Grep/Edit loop against the raw Anthropic API.
  - `agent_tools.py`: sandboxed read_file/list_files/grep/edit_file/write_file, all confined to one project root
    via path-resolution + ancestry check (PathEscapesRootError) — a real security boundary, not a convenience
    check, since these calls are driven by model output that can be wrong. edit_file matches Claude Code's own
    Edit tool contract exactly: old_string must be unique in the file or the call is refused (EditAmbiguousError/
    EditNotFoundError), never a silent replace-all or a guess.
  - `agent_loop.py`: the provider-agnostic turn loop (call model -> dispatch any tool_use requests -> feed
    tool_results back -> repeat until a plain-text answer or max_turns). `call_model` is injected, so the loop's
    own logic — including CHEK_PROTOCOL.md's read-only-role enforcement (`allowed_tools=READ_ONLY_TOOLS` makes an
    edit_file call from a checker/critic actually fail, not just get told not to in its prompt) — is fully unit
    tested with a scripted fake model, no live API calls.
  - `providers/claude.py` gained `run_agentic_task()`: the real Anthropic tool-use adapter. Deliberately NOT added
    to the AIProvider ABC (see base.py's docstring) — its shape doesn't fit probe()/run_task(), and a provider
    without tool-use support genuinely cannot offer it. The response-to-ModelTurn conversion was pulled out of the
    closure into a standalone `_response_to_model_turn()` specifically so it has its own unit tests against fake
    SDK response objects, instead of being untestable without a live call (which is why probe()/run_task()
    themselves still have no direct unit tests, per the earlier session-log note — this one does, because the
    block-type/tool_use extraction logic is genuinely more complex and worth pinning).
  28 new tests (91 total), all green. Still not started: the actual Steps 5-12 role prompts/orchestration
  (planner, checkers, fixer, critics, the convergence loop) that will call run_agentic_task — this session built
  the engine, not the fleet itself yet.

- 2026-08-23 (same day, later) — Step 5, the fleet planner, is real. Two new modules:
  - `chek_protocol_text.py`: splits CHEK_PROTOCOL.md into {section title: body} by its own '# ===\n# TITLE\n#
    ===' header convention, and extracts fenced ``` prompt blocks from a section. Prompts are read out of the MD
    file at runtime, never copied into a Python string — CHEK_PROTOCOL.md's own header calls itself "the ONLY
    copy of the protocol body", and a hardcoded copy would silently drift the next time an AI-kit sync pulls an
    update from the structure repo. Verified against the REAL file, not just a synthetic sample: parametrized
    tests assert the exact fenced-block count CHEK_PROTOCOL.md actually has for Steps 5/8/9/10/11/12 (1/1/1/2/3/1
    — checked by hand with `awk` against the live file before writing the assertions, not guessed).
  - `chek_fleet.py`: `run_fleet_planner()` runs Step 5 as one read-only run_agentic_task call with the extracted
    planner prompt, and `parse_planner_output()` parses its DOMAIN/PROMPT/SUMMARY output into a FleetSpec. Noted
    as best-effort (PlannerOutputError on no parseable domains) since, unlike chek_registry.py's strict
    self-authored format, this parses free-form model output against a template the model might not follow
    exactly.
  Critic pass caught a real bug in `find_section()` before it shipped: a bare `.startswith()` prefix match means
  "STEP 10"/"STEP 11"/"STEP 12"/"STEP 13" all start with the literal string "STEP 1", so `find_section(sections,
  "STEP 1")` was one dict-ordering coincidence away from silently returning the wrong section — it "worked" only
  because Step 1 happens to appear before Step 10 in the real file, protecting it by luck rather than by being
  correct. Proved this two ways: reproduced the wrong-section return with the old logic against a deliberately
  reordered dict (Step 10 inserted before Step 1), then confirmed the word-boundary fix (`title == prefix or
  title.startswith(prefix + " ")`) returns the right section regardless of dict order. The obvious "test against
  the real file" version of this regression test would NOT have caught it — natural document order shields the
  bug there too — so the real pin is the adversarial-order unit test, not the real-file one (kept anyway, for
  the block-count coverage above).
  22 new tests (114 total), all green. Next: Step 6 — parallel checkers per the planner's domain spec, reusing
  jobs.py's live-status engine (do not build a second one) — then Step 7's coverage check. See LAST_PROMPT.md.
