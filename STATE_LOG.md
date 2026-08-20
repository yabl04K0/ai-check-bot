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
