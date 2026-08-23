# STATE_LOG — append-only machine log of ai-check-bot runtime state (LLM-ONLY, English on purpose)

FORMAT: LLM only. Flat text, `key: value`, facts. NOT for humans — no decoration, no tables, no prose.
APPEND: new entries go AT THE END. Never rewrite an old entry (the log is history). The first entry is the oldest.
ENTRY: each entry starts with `--- [PREFIX] YYYY-MM-DD HH:MM МСК (HH:MM UTC) ---`, then `key: value` lines.
TIME: always date AND time (МСК = UTC+3). State the moment of OBSERVATION, not only the moment of writing.
WHEN TO WRITE:
  (1) [STATE] — every time fresh information about bot/runtime state is known (none yet: no runtime exists).
  (2) [CHEK]/[BETA] — on EVERY CHEK run and on every soak/beta test of the bot.
  (3) other prefixes — per event (see PREFIXES).
LANGUAGE: English for new entries.

PREFIXES:
  STATE    — snapshot of bot/runtime at a moment (alive? responsive? which providers connected).
  CHEK     — a CHEK run: scope, outcome, open findings count.
  BETA     — a soak/dev test: which build, how it behaved.
  DEPLOY   — code deployed: what, method, restart, verification.
  NET      — network / provider-API / Telegram / GitHub connectivity check.
  INCIDENT — observed problem (symptom + root if known).
  FIX      — applied fix (what it closed).
  TRIED    — attempt that did NOT work: what, why, observed failure. CHECK before retrying an idea.
  DM       — notification to the user (to whom, what, delivered via bot or chat-only).
  HANDOVER — end-of-session summary: what was done, current state, STOPPING POINT, what is next, open questions.
             Written when the conversation ends or looks likely to end (see AI_COMMANDS.md HANDOVER).
  AI_KIT   — an AI-kit sync: which files, source repo SHA, applied vs skipped-as-local-override.

# === entries below (append) ===

--- [HANDOVER] 2026-08-20 00:00 МСК (2026-08-19 21:00 UTC) ---
what: initial AI-collaboration doc stack ported from yabl04k0/MeCelium and yabl04k0/AutoPost onto branch
  claude/md-structure-bot-setup-p1gcn7, adapted for Claude Code (no Cursor CLI) and for this repo's no-code-yet
  state. CHEK audit explicitly not run this session (user deferred it).
state: repo is still scaffold-only — README.md is the spec, no bot code exists.
stopping_point: docs committed and pushed; next session should read PROJECT_MEMORY.md then AI_COMMANDS.md and
  decide whether to start on the `AIProvider` interface or on a specific bot feature the user names.
open_questions: whether/when to build the configurable-message + scheduled-provider-probe feature described by the
  user outside this doc-porting task (tracked in LAST_PROMPT.md if authored).

--- [HANDOVER] 2026-08-23 21:01 МСК (18:01 UTC) ---
what: same chat as the previous [HANDOVER], continued over several more turns. Built out the bot's feature
  backlog end to end (AIProvider abstraction, ClaudeProvider, scheduled per-account health probes <=5/day,
  per-account proxy, multi-account pooling via providers/router.py, jobs.py live-status/cancel engine, inline-
  keyboard menu, a working custom-task dispatch), then started the CHEK audit fleet itself: chek_registry.py
  (Step 1 load/dup-check, Step 13 append/remove), chek_scan.py (Step 2 tests, Step 4 grep sweep), a real tool-use
  agent loop (agent_tools.py sandboxed file tools + agent_loop.py provider-agnostic turn loop +
  ClaudeProvider.run_agentic_task), chek_protocol_text.py (extracts prompts from CHEK_PROTOCOL.md at runtime,
  never duplicated), and chek_fleet.py Steps 5 (fleet planner) + 6 (parallel checkers, jobs.run_workers_parallel).
  Also created and populated two sibling repos: yabl04K0/0000 (private ai-dev-kit structure repo) and yabl04K0/1111
  (public CHEK-protocol mirror). Every increment: implemented, critic-read, tested, committed, pushed — full
  detail (including every real bug the critic pass or a test caught, and why) is in PROJECT_MEMORY.md's session
  log, one entry per increment; this HANDOVER does not repeat it.
state: all code committed and pushed to origin/claude/md-structure-bot-setup-p1gcn7 (HEAD 5548bb5). Working tree
  clean. 129/129 tests green (`PYTHONPATH=src pytest -q`, or plain `pytest -q` with the venv active — see
  CLAUDE.md "Commands"). No PR opened (not asked). Bot has never been run live (no BOT_TOKEN in this sandbox) —
  all correctness evidence is from the test suite, not a live Telegram session.
stopping_point: CHEK_PROTOCOL.md Steps 1, 2, 4, 5, 6, and 13's write-back are real and tested. Steps 3 (deploy
  state) and 4b (web research) are legitimately skippable when no deploy target/research need exists — not gaps.
  Steps 7-12 (aggregation + coverage check, gap-finder, fixer, two critics, convergence loop, test-writer + the
  mandatory git-stash check) are NOT started. LAST_PROMPT.md has the concrete next-step plan, written specifically
  so the next session does not have to re-derive it: start with Step 7 (chek_fleet.py, pure orchestrator logic —
  no agent call — coverage check via agent_tools.list_files vs the union of every CheckerReport.files_read, then
  merge/dedupe findings by severity).
open_questions:
  1. yabl04K0/1111 (public chek-protocol repo) is still PRIVATE — no path exists from this sandboxed environment
     to flip GitHub repo visibility (MCP tool lacks Administration permission; the session's network proxy blocks
     all raw GitHub write calls outside the allowed MCP tools — verified by direct testing, not assumed). The
     user has the ready-to-paste prompt for a GitHub-connected session to do it (given twice in this chat).
  2. Model routing gap flagged in LAST_PROMPT.md: CHEK_PROTOCOL.md calls for opus-level judgment on the planner
     and both critics specifically; this bot only has one AGENT_MODEL constant (claude-sonnet-4-5) wired in
     providers/claude.py. Worth deciding whether to add a second model tier before Step 10 (critics) if not
     addressed earlier while building Step 7-9.
  3. Whether to keep pushing straight to this feature branch or open a PR — not asked either way; no action taken.
