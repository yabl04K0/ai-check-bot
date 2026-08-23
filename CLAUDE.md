# CLAUDE.md — binding rules for AI working on ai-check-bot (LLM-ONLY, English on purpose)

This file is loaded into every session. It holds RULES. It does not hold command triggers, the CHEK protocol, the
branch model, or project state — those live in one place each (see POINTERS). Never duplicate their content here.

## POINTERS — one source per topic

AGENTS.md          — thin pointer to this file, kept for tools that read AGENTS.md by convention. Never a second copy.
AI_COMMANDS.md      — every user command trigger («промпт», ЧЕК, review, handover, DM) and what it does. THE authority.
CHEK_PROTOCOL.md    — the ЧЕК audit protocol, steps 1-13, agent prompts, roles, models. SYNCED, see AI-KIT below.
DELEGATION.md       — HOW the fleet runs in THIS environment: Claude Code's own Agent tool, no external CLI.
BRANCHING.md        — main/beta/alpha model, branch prefixes, GATE-CONFIDENT (when a fix may go straight to main).
TOKEN_ECONOMY.md    — which model does what, the `.claude/agents` fleet, the read budget, `.claude/settings.json`.
PROJECT_MEMORY.md   — project structure, patterns, invariants, schema, session log. ALWAYS read at session start —
                      but the sections ABOVE the SESSION LOG only. The log is history; read it when asked about it.
LAST_PROMPT.md      — single store of the last AI-authored prompt (body of the «промпт» command).
STATE_LOG.md        — append-only machine log of bot/runtime state, deploys, incidents, failed attempts, handovers.
                      Tail it for the newest entries; NEVER read it whole.
TROUBLESHOOTING.md  — provider API / Telegram / GitHub egress journal + diagnostics. Read on any connectivity symptom.
chek_open.md · chek_never.md · chek_later.md — CHEK problem registry. A problem lives in exactly ONE of them.
docs/*.md           — HUMAN architecture diagrams. Read when the task touches architecture/UX; not command authority.
README.md           — HUMAN file. It is ALSO the current product spec (this repo has no code yet, so README is the
                      closest thing to a "structure" section until PROJECT_MEMORY.md accumulates one). Read it for
                      product intent; do not treat it as AI command authority.
.claude/commands/   — slash stubs (`prompt`, `chek`) that ONLY point at AI_COMMANDS.md; never redefine triggers here.
tools/ai_kit.json    — which files are synced from the structure repo, and the override rule. See AI-KIT below.

## SIBLING PROJECTS (reference examples of the doc family this bot's own product manages)

  yabl04k0/MeCelium     — Telegram bot, VPN-reseller domain. Most mature copy of this doc stack (Windows/Cursor CLI
                          specifics there do not apply here — this repo runs in Claude Code on Linux).
  yabl04k0/AutoPost      — Telegram bot, e621 auto-poster domain. Same doc stack, ported from MeCelium.
  yabl04k0/sd-forge-bot  — Telegram bot, Stable Diffusion Forge. Lighter stack: only chek_*.md + chek.md.
This bot's product is a dispatcher that runs this exact protocol across projects like these three — reading a
sibling's PROJECT_MEMORY.md before assuming one of ITS patterns applies here is still correct; application-level
code is never auto-copied between repos.

## CRITICAL: the AI-kit is synced from a dedicated structure repo, not authored fresh per project

This repo's AI-collaboration docs (the TIER A list in `tools/ai_kit.json`: CLAUDE.md, AGENTS.md, AI_COMMANDS.md,
CHEK_PROTOCOL.md, DELEGATION.md's role/model table, BRANCHING.md, TOKEN_ECONOMY.md, `.claude/agents/*`,
`.claude/commands/*`, `agents/web-researcher.md`) are maintained centrally in the private structure repo and synced
here, not authored independently per project. CHEK_PROTOCOL.md is the most actively iterated file in the kit and is
kept continuously current in the structure repo — it is NOT a "set once" file the way BRANCHING.md is.
DEFAULT AUTHORITY: the structure repo wins on drift. NEVER overwrite the structure repo from this project's copy
just because this copy changed — that is backwards and turns local experiments into upstream policy.
OVERRIDE ESCAPE HATCH: a project MAY declare itself authoritative for a specific file or a specific rule inside a
file — but ONLY via an explicit `<!-- ai-kit: local-override reason="..." -->` marker directly above the diverging
section. No marker = no override = the structure repo wins on the next sync, silently reverting the drift.
NO BLIND SYNC: syncing means diff-and-review, never a blind overwrite. Pull the structure repo, diff each TIER A
file against this project's copy, and apply only what is not covered by a local-override marker. A sync that touches
a file with no real drift is a wasted commit — skip it.
COMMIT DISCIPLINE: a sync commit is minimal and factual — "sync AI-kit: <file> — <one-line reason>", never a vague
"update docs". Prose in every AI doc here follows the same rule: precise, complete, no padding — write it the way a
well-run open-source project writes its own docs, not the way a rough internal note gets written.
WHEN: check for structure-repo drift before ending a session that touched a TIER A file, and whenever the user asks
for a sync explicitly. Do not poll for drift on unrelated sessions — that is cost with no signal.

## How to write a rule in this file

Every rule needs four parts, or the AI will not apply it to a case you did not enumerate:
  SIGNAL first — `NEVER` / `ALWAYS` / `CRITICAL`. The first word sets how the AI weights the rule.
  REASON — without "why", the rule is not transferred to non-standard cases.
  WHAT BREAKS — the concrete failure this prevents.
  COUNTEREXAMPLE — the "wrong" next to the "right" when helpful.
Bad:  "Don't hardcode Claude."             (the AI will not know why, and will violate it in an edge case)
Good: "NEVER call the Anthropic SDK directly from a handler — route through AIProvider, or Codex/Cursor/local-LLM
       support requires re-abstracting every call site under time pressure."

## Doc-writing rules

ALL AI files (listed under POINTERS, except README.md and docs/*.md) are LLM-ONLY and written in ENGLISH: flat text,
`key: value`, short paragraphs, direct statements. NO tables, NO decorative headings, NO formatting for its own sake.
Reason: the AI reads text, it does not render markdown — extra markup is noise, and tables destroy diff readability.
Product docs under docs/ ARE for humans; keep their own style, do not convert them to LLM-flat.
ALWAYS timestamp work records with date AND time: `YYYY-MM-DD HH:MM МСК` (MSK = UTC+3).

ALWAYS update PROJECT_MEMORY.md after implementing a feature or changing the schema, and commit it with the code WHEN
the user asks to commit. Reason: the next session (or another chat) does not know what was done; a stale doc is
worse than none. NEVER skip the doc update because the change "looks small" — drift always starts there.
NEVER claim something is in git if `git log`/`git status` says otherwise — say "on disk" / "working tree".

## Project language

Bot UI text, code comments and runtime log messages: Russian. Identifiers (variables, functions): English.
AI docs: English. Chat with the user: Russian.

## CRITICAL: session handover (chat switch)

ALWAYS fire AI_COMMANDS.md command HANDOVER when the session is ending or looks likely to end — including when the
user says they will open another chat, will not reopen this one, or says «хендовер».
Reason: the next chat has zero context; without STATE_LOG [HANDOVER] + PROJECT_MEMORY session log + LAST_PROMPT +
chek_* registries, the next AI re-discovers and re-breaks work.
NEVER answer only in chat prose and skip the files — the chat is ephemeral; the MD stack is the continuity.

## CRITICAL: secrets

NEVER commit `.env`, provider API keys (Anthropic/OpenAI/Cursor), BOT_TOKEN, GitHub fine-grained tokens, or a
provider CLI's session/auth state.
NEVER paste a live token or key into a chat reply. A token that leaked in chat must be rotated immediately — tell
the user which provider's dashboard to rotate it in.

## CRITICAL: provider abstraction

NEVER call a provider SDK (Anthropic/OpenAI/Cursor CLI/local endpoint) directly from a bot handler or job. Every
call goes through the `AIProvider` interface (README "Провайдеры ИИ"). Reason: the product's entire pitch is
multi-provider with per-provider quota/auth; a hardcoded call blocks the other three providers and forces a risky
retrofit later. NEVER treat "Claude" as a synonym for "the AI provider" anywhere in code or naming.

## CRITICAL: human-in-the-loop on commit and push

NEVER let a delegated task (any provider, any mode) run `git commit` or `git push` on its own — the human confirms,
always, on every project this bot manages, including this repo itself.
CRITICAL: self-check (this repo audited by itself) additionally NEVER auto-pushes even where auto-push is enabled
for other managed projects — a patch that breaks the bot mid-apply must not ship itself.

## CRITICAL: repo deletion is structurally unreachable

NEVER add a code path — bot handler, `AIProvider` method, or GitHub module function — capable of deleting a
repository or rewriting/discarding its commit history. Visibility toggling is fine; `delete_repo` scope and any
history-rewriting git command triggered by the bot are not. This is a code-level guarantee the tests must assert,
not a runtime permission check that a bug can route around.

## Feature implementation workflow

ALWAYS follow this sequence when a request clearly implies implementation (not discussion) — even for a request that
looks trivial:
1. Read every file the feature will touch, IN FULL, not by grep. Before writing any code.
2. Implement it.
3. ALWAYS switch to critic mode: re-read every changed file top to bottom hunting wrong assumptions, missed edge
   cases, rule violations, inconsistency with surrounding code. Do this BEFORE running tests, as a separate step.
   Say in the chat which files you re-read and at least two specific things you checked. You MAY delegate this pass
   to the `reviewer` subagent — the evidence rule is unchanged either way.
4. Fix everything found in step 3.
5. Run the test suite (`pytest -q`, or the current command in TOKEN_ECONOMY.md/"Commands"). Fix failures. Route
   through `runner` when the transcript is long.
6. Stage/commit ONLY when the user asks. Ask "Готово, коммитить/пушить?" if a commit is the natural next step and
   none was requested.
Do not stop between steps for confirmation unless the requirement itself is fundamentally unclear.
If the request is exploratory («что если», «как лучше», "should we") — discuss only, do not implement.

## Minimal-code ladder (binding for CHEK work, good practice everywhere)

ALWAYS write the minimum code. Before writing a line, walk the ladder top to bottom; write code only when every
rung answers "no":
1. Is it needed at all? Speculative "we might need it" → skip it, say so in one line (YAGNI).
2. Does the project already have it? A helper, type or pattern next door → reuse it. Read the code first.
3. Can the standard library do it?
4. Does an already-installed dependency solve it? Never add a dependency for two lines of work.
5. Does it fit on one line? Write one line.
6. Only now: the minimal code that works.
"Minimal" NEVER means cutting validation, error handling or security. NEVER widen a bare `except:` to silence a
reviewer. NEVER delete a test to make CHEK green.

## Commands (local)

Setup: `python -m venv venv && source venv/bin/activate && pip install -r requirements-dev.txt`
Tests: `PYTHONPATH=src pytest -q` (pyproject.toml also sets `pythonpath = ["src"]`, so plain `pytest -q` from the
  repo root works once the venv is active). Healthy collection as of the last run: 91 tests, all green.
Bot: `PYTHONPATH=src python -m ai_check_bot.bot` (needs `BOT_TOKEN` and `ADMIN_TG_ID` in `.env`; AI-account
  credentials are added at runtime via the bot's own menu, not `.env` — see `src/ai_check_bot/bot.py`).
Package layout: `src/ai_check_bot/` (src-layout, not a flat package) — `config.py`, `db.py`/`models.py`,
  `providers/` (the `AIProvider` interface + implementations + `router.py` multi-account pooling, registered in
  `providers/registry.py`), `probe_service.py`/`task_service.py` (business logic), `scheduler.py` (APScheduler,
  UTC), `jobs.py` (live-status/cancel job engine), `chek_registry.py` (chek_open/never/later.md parse+rewrite),
  `chek_scan.py` (CHEK Steps 2/4: test runner, grep sweeps), `agent_tools.py` (sandboxed Read/Glob/Grep/Edit/Write
  for a target project root) + `agent_loop.py` (provider-agnostic tool-use loop — CHEK Steps 5-12's mechanics),
  `ui.py`/`keyboards.py`/`input_flow.py` (bot UI), `bot.py` (Telegram entrypoint).
All files UTF-8.
