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
  — see PROJECT_MEMORY.md's session-log entries for exactly what and how. Do not re-implement any of it; read the
  log before touching provider/account/job/agent-loop code so you do not duplicate what already exists.

READ FIRST: PROJECT_MEMORY.md (all session-log entries about chek_registry/chek_scan/agent_loop in full) ->
  AI_COMMANDS.md -> latest STATE_LOG.

## STATE: what exists and works (91/91 tests green, `pytest -q`)
  AIProvider abstraction + ClaudeProvider (probe + run_task + run_agentic_task), per-account proxy, multi-account
  pooling via providers/router.py, scheduled health probes (<=5/day/account), jobs.py (live status, cooperative +
  real asyncio-level cancel), an inline-keyboard menu (bot.py + keyboards.py), a working "✨ Новая задача" custom-
  task dispatch, chek_registry.py (Step 1 load + duplicate check, Step 13 append/remove), chek_scan.py (Step 2
  test runner, Step 4 grep sweep), and — the newest piece — a real tool-use agent loop: agent_tools.py (sandboxed
  read_file/list_files/grep/edit_file/write_file, Edit-tool-exact unique-match semantics, path-escape blocked) +
  agent_loop.py (provider-agnostic turn loop, read-only-role enforcement via allowed_tools, unit tested with a
  scripted fake model) + ClaudeProvider.run_agentic_task (the real Anthropic tool-use adapter).

## GOAL — Steps 5-12: the actual fleet orchestration, now that the engine exists

Steps 1, 2, 4, and 13's write-back are real. Step 3 (deploy state) and 4b (web research) are legitimately skippable/
deferrable per the protocol itself when no deploy target or research need exists. What's left is Steps 5-12 —
CHEK_PROTOCOL.md's own text for each step IS the spec; the prompts quoted there are meant to be pasted close to
verbatim as `system_prompt`/`user_prompt` to `provider.run_agentic_task(root, system_prompt, user_prompt,
allowed_tools=..., max_turns=...)`:
  5. Fleet planner — ONE run_agentic_task call, allowed_tools=agent_loop.READ_ONLY_TOOLS, the Step 5 planner prompt
     verbatim. Parse its DOMAIN/PROMPT/SUMMARY output into a structured fleet spec (new code — a small parser,
     similar in spirit to chek_registry.py's parsing but for this different, less strictly-formatted output).
  6. Fleet checkers — one run_agentic_task call per domain from the planner's spec, IN PARALLEL
     (`asyncio.gather`), allowed_tools=READ_ONLY_TOOLS, no exceptions. This is the one place jobs.py's
     multi-worker live-status rendering is exactly what's needed — reuse jobs.create_job/run_workers, do not
     build a second status system. Coverage check (Step 7) after: Glob vs the union of "Прочитано:" lines each
     checker's final_text ends with.
  8. Gap-finder — ONE call, READ_ONLY_TOOLS, the Step 8 prompt with the aggregated Step 7 report pasted in.
  9. Fixer — ONE call, allowed_tools=agent_loop.ALL_TOOLS (this is the one role allowed to edit), the Step 9
     prompt. Run chek_scan.run_tests() afterward, not the fixer itself.
  10. Two critics — TWO run_agentic_task calls IN PARALLEL, READ_ONLY_TOOLS, the two different Step 10 prompts.
  11. The convergence loop — CHEK_PROTOCOL.md spells out the exact loop (MAX_PER_PROBLEM/MAX_GLOBAL, when to use
      the cheaper scoped verifier vs the full two critics) — implement it as literally described, do not simplify
      the oscillation detector away.
  12. Test-writer — ONE call, ALL_TOOLS, the Step 12 prompt, THEN the mandatory git-stash check (chek_scan.py has
      no stash helper yet — small addition needed: stash, run_tests, pop, run_tests again, compare).
  13. Already have the registry write-back (chek_registry.py); still need: showing the human the totals/diff/test
      result and waiting for explicit confirmation before git add/commit/push (human-in-the-loop, never an agent).
Model routing per CHEK_PROTOCOL.md: this bot only has Claude, and only one model tier is wired
(AGENT_MODEL=claude-sonnet-4-5 in providers/claude.py) — decide whether to add a second AGENT_MODEL_OPUS-style
constant for the planner/critics (protocol says opus) before starting Step 5, since that's a real, visible
correctness gap otherwise (protocol calls for opus-level judgment on the planner and critics specifically).
Do not rush a half version of this — a fake/simplified "fleet" that doesn't follow CHEK_PROTOCOL.md's roles/gates
is worse than not having one, per this project's own minimal-code-ladder and the protocol's own FORBIDDEN list (no
collapsing the fleet into one call, no skipping the coverage check, no agent ever committing).

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
