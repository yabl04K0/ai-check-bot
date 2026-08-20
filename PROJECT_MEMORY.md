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
  (`pytest -q`). Not committed yet — pending the user's go-ahead per CLAUDE.md's commit rule.
