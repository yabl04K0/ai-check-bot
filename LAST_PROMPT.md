# LAST_PROMPT — LLM-ONLY. SINGLE CANONICAL STORE of the LAST prompt the AI authored. (English on purpose; NOT for humans.)
#
# TRIGGERS ARE DEFINED IN AI_COMMANDS.md — that file is the authority; this header only summarizes:
#   PROMPT_RUN   (bare «промпт»/`prompt`)   -> EXECUTE the body below as if the user had typed it. This is the default.
#   PROMPT_SHOW  («покажи промпт»)          -> print the body below without executing anything.
#   PROMPT_WRITE («сделай/дай промпт ...»)  -> author a new prompt and OVERWRITE this file (keep this header block),
#                                              so exactly ONE stored "last prompt" exists at any time.
# No prompt body below the separator -> tell the user there is no stored prompt.
#
# EXPLANATION lines below state WHY, so the executing AI applies intent to edge cases, not just literal commands.
# ================= CURRENT PROMPT BODY =================

ROLE: Continue ai-check-bot. Chat in the user's language; AI docs stay English.
CONTEXT: two sibling repos exist and are populated: yabl04K0/0000 (private ai-dev-kit structure repo) and
  yabl04K0/1111 (public CHEK-protocol-only mirror — still needs its GitHub visibility flipped to public by hand,
  no API path from this environment can do it). This repo's feature backlog from the doc-porting session is DONE
  — see PROJECT_MEMORY.md's last two session-log entries for exactly what and how. Do not re-implement any of it;
  read the log before touching provider/account/job code so you do not duplicate what already exists.

READ FIRST: PROJECT_MEMORY.md (both recent session-log entries in full) -> AI_COMMANDS.md -> latest STATE_LOG.

## STATE: what exists and works (34/34 tests green, `pytest -q`)
  AIProvider abstraction + ClaudeProvider (probe + run_task), per-account proxy, multi-account pooling via
  providers/router.py (least-recently-used), scheduled health probes (<=5/day/account, configurable time+message),
  jobs.py (live status, cooperative cancel for multi-worker loops, REAL asyncio-level cancel for a single call),
  an inline-keyboard menu (bot.py + keyboards.py, patterns borrowed from sd-forge-bot/MeCelium — see
  PROJECT_MEMORY.md for which file each pattern came from), and a working "✨ Новая задача" custom-task dispatch
  end to end (pick account -> run_task -> live status -> cancellable -> result shown, truncated to Telegram's
  4096-char limit).

## GOAL — the one piece big enough to be its own project, not a backlog line item

Build the actual CHEK audit fleet (README "Режимы аудита", full protocol in CHEK_PROTOCOL.md) driven from the bot:
  1. A way to point the bot at a target GitHub repo (this repo itself, or another one the user manages) and start
     a Full/Lite ЧЕК run against it.
  2. Spawning/monitoring the actual multi-step CHEK_PROTOCOL.md fleet (Steps 1-13) for a provider — start with
     Claude only, matching what AIProvider already supports; each checker/critic/fixer call is itself a
     provider.run_task()-shaped call, but the ORCHESTRATION (fleet planning, coverage checks, the convergence
     loop) is new work, not something jobs.py already does.
  3. Wire the fleet's live progress through jobs.py (it already renders "which/how many workers active" and
     supports cancel — reuse it, do not build a second status system) and through GitHub for the actual commit/
     push at Step 13 (human confirms, per CLAUDE.md's "CRITICAL: human-in-the-loop").
  4. chek_open.md/chek_never.md/chek_later.md registry read/write from the bot side, per CHEK_PROTOCOL.md Steps 1
     and 13.
This is a genuinely large, multi-session effort — plan it before writing code (CLAUDE.md's own feature workflow:
read everything the feature touches in full before implementing). Do not rush a half version of it; a fake/
simplified "fleet" that doesn't actually follow CHEK_PROTOCOL.md's roles/gates is worse than not having one, per
this project's own minimal-code-ladder and CHEK_PROTOCOL.md's FORBIDDEN list (no collapsing the fleet into one
call, no skipping the coverage check, no agent ever committing).

## SMALLER FOLLOW-UPS (worth doing, none of them urgent)
  Codex/Cursor/local-LLM AIProvider implementations — one module + one registry.py line each, per that file's
    own docstring; nobody has written them yet.
  Encrypted credential storage — AIAccount.api_key is plaintext in SQLite today (flagged inline in models.py).
  Persisted task/probe history beyond a job's own live state (README "История задач" admin screen).

## HARD RULES (unchanged)
  NEVER call a provider SDK directly from a handler — always through AIProvider.
  NEVER let any delegated task run `git commit`/`git push` on its own; human confirms, including for this repo.
  NEVER add a code path capable of deleting a repo or rewriting its history.
  NEVER collapse the CHEK fleet into one agent/call "to save time" — CHEK_PROTOCOL.md forbids it explicitly and
    means it: a single pass skims and finds nothing, which defeats the entire point of the protocol.

# EXPLANATION: the user said "не заканчивай пока не доделаешь все что я просил" (don't stop until everything
# asked for is done) partway through this session. Every concretely-scoped backlog item got a real, tested
# implementation as a result — see PROJECT_MEMORY.md. What's left (the CHEK fleet) was never a discrete ask; it's
# the bot's whole reason to exist per README, and pretending to finish it in the same sitting as everything above
# would mean shipping something that violates CHEK_PROTOCOL.md's own rules about not faking the fleet. This file
# hands the next session a clear, honestly-scoped starting point instead.
