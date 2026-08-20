# TOKEN_ECONOMY — what runs on which model, and what never enters the main context (LLM-ONLY, English on purpose)

PURPOSE: the expensive model must spend its tokens on judgement, not on clerical work. This file owns the routing
  rules (which model does what), the read-budget rules (what may enter the main context), and the rationale for
  `.claude/settings.json`. It owns no protocol content and no project state.
AUTHORITY: this file is THE source for delegation inside a Claude Code session.
NOT THIS FILE: DELEGATION.md owns HOW the CHEK fleet's role-to-Agent-call mapping works. Two different concerns:
  this file is about in-session subagents for ROUTINE dev work (`.claude/agents/`); DELEGATION.md is about the
  CHEK protocol's own fleet, which also runs on the Agent tool but with different roles and models.

# ============================================================================
# THE COST MODEL — why any of this matters
# ============================================================================

Three things cost tokens, in this order of size:
  1. OUTPUT tokens of the top model — thinking, tool calls, and their arguments. Dominant cost, scales with effort
     level, not just answer length.
  2. Everything re-sent on EVERY turn — CLAUDE.md, tool and skill schemas, the whole conversation so far.
  3. Raw tool output pasted into the main context — test transcripts, logs, whole large files.

Therefore the three levers, same order: run routine work on a cheaper model; keep the always-resent block small;
never let bulk output touch the main context.

WHAT BREAKS IF IGNORED: the session hits its usage limit mid-task, auto-compacts, and the compacted summary loses
the working detail the fix depended on. The next turn re-derives it wrongly. Running out of budget is a
correctness problem, not a billing one.

# ============================================================================
# ROUTING — the top model decides, the cheap models execute
# ============================================================================

ALWAYS keep on the top model. Reason: a wrong call here is expensive to undo, and these are exactly the places
  where a cheaper model's plausible-but-wrong answer is hardest to spot.
  Architecture and provider-abstraction decisions; choosing between approaches.
  Anything a double-run would corrupt: quota/balance math, a Telegram message sent to a real user, a GitHub
    visibility change, a commit.
  DB schema decisions and migration design.
  Security, secrets, auth wiring for any of the four AI providers or GitHub.
  Root-cause analysis of a bug whose cause is not yet known.
  Reading the user's intent, and every reply written to the user.
  The final go/no-go before a commit.

ALWAYS delegate to a mid-tier model (`builder`, `reviewer`) — normal engineering work that is already decided.
  Implementing a change the top model has already specified, file by file.
  Writing tests from a stated contract.
  Mechanical refactors and renames across several files.
  The critic re-read of changed files.

ALWAYS delegate to the cheapest model (`scout`, `runner`, `scribe`) — mechanical work with an objective answer.
  Finding where something lives.
  Running tests and commands, and filtering their output.
  Appending entries to the MD stack.

# ============================================================================
# THE FLEET — .claude/agents/, five subagents
# ============================================================================

scout    (haiku)  WHERE is it. Any "which files touch X" sweep. Returns path:line lists, max 40 lines.
runner   (haiku)  RUN it. Every test run and every noisy command. Returns counts + failing tracebacks only.
scribe   (haiku)  WRITE IT DOWN. STATE_LOG, PROJECT_MEMORY session log, chek_* registries. Caller supplies facts.
builder  (sonnet) BUILD the already-specified change. Never designs.
reviewer (sonnet) BREAK it. Adversarial re-read of changed files, before tests. Never edits.

Each subagent runs in its OWN context. Only its final report returns to the main thread — that is the whole point.
A large test transcript costs the main context a few lines, not thousands.

DELEGATION IS NOT FREE. A subagent re-reads what it needs from scratch, so a task smaller than roughly one file
read is cheaper done inline. NEVER delegate a single Read of a known path, a one-line edit, or a question you can
already answer.

NEVER delegate the decision itself. `builder` gets "add column X to table Y and register it in the migration list",
never "figure out how to store X". If you cannot state the task precisely enough for a subagent, you have not
finished thinking.

NEVER confuse this fleet (`.claude/agents/`, in-session, routine dev work) with the CHEK fleet (DELEGATION.md,
CHEK_PROTOCOL.md — audit-specific roles, different prompts, different models per step).

# ============================================================================
# READ BUDGET
# ============================================================================

ALWAYS read at session start: CLAUDE.md (automatic) and the PROJECT_MEMORY.md sections above the session log —
  structure, patterns, invariants, schema.
NEVER read at session start: the PROJECT_MEMORY.md SESSION LOG, STATE_LOG.md, CHEK_PROTOCOL.md, chek_open.md,
  README.md (unless the task is product scope), docs/**. Each is history, protocol, or product spec; needed only
  when the task is about that.
ALWAYS read on demand, and only the part you need:
  CHEK_PROTOCOL.md — when running ЧЕК. chek_open.md — when the task is a registry item.
  STATE_LOG.md — tail it for the last entries; NEVER read it whole. TROUBLESHOOTING.md — on a connectivity symptom.
  AI_COMMANDS.md — when a user command trigger fires.
ALWAYS prefer Grep over Read on any file over ~500 lines: find the section, then read that range with offset+limit.
NEVER re-read a file you already read this session unless you changed it or a subagent changed it.

# ============================================================================
# SESSION HYGIENE
# ============================================================================

/clear between unrelated tasks — a finished task's context is pure cost on every later turn.
/compact only mid-task when the context is genuinely needed but too large.
/effort — raise for a CHEK audit, a correctness bug, or an unexplained failure; drop for mechanical work.
/model — the session driver stays on the top model. For a session that is entirely routine, switching the whole
  session to a cheaper model costs far less than delegating repeatedly from the top model.

# ============================================================================
# WHAT .claude/settings.json DOES — do not "simplify" these away once set
# ============================================================================

CLAUDE_CODE_SUBAGENT_MODEL sonnet — any delegated pass without an explicit model lands on Sonnet, not on the
  session model. Without it, delegation silently costs top-model rates and saves only context, not budget.
BASH_MAX_OUTPUT_LENGTH / MAX_MCP_OUTPUT_TOKENS — hard caps so one runaway command cannot flood the context.
permissions.deny on archives/binaries/caches — those reads are never useful and can be enormous.
permissions.ask on *.log and images — a speed bump, not a ban.
permissions.allow on read-only git and test commands — every permission prompt interrupts a turn and re-sends
  context.

# ============================================================================
# THE FLOOR — what economy must NEVER touch
# ============================================================================

NEVER skip reading a file in full before changing it. Economy applies to what you carry, never to what you check.
NEVER skip the critic pass, the tests, or the doc update. Delegate them; do not drop them.
NEVER let a subagent's report stand in for a decision the top model should make.
NEVER cut validation, error handling or security to make a diff smaller.
NEVER delete tests, widen a bare except, or trim a registry to make something look green.
If economy and correctness ever conflict, correctness wins and you say so in one line.
