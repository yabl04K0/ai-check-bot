# DELEGATION — how the CHEK fleet actually runs in this repo (LLM-ONLY, English on purpose)

PURPOSE: state the ONE mechanism this project uses to run the CHEK fleet (and any other multi-agent work). The
  roles, steps and gates do NOT change — CHEK_PROTOCOL.md stays the only protocol body. This file owns no protocol
  content and no command triggers — those live in CHEK_PROTOCOL.md and AI_COMMANDS.md.
CONTEXT: the sibling projects (MeCelium, AutoPost) that this doc stack was ported from run a separate Cursor Agent
  CLI fleet via PowerShell wrapper scripts on Windows. That mechanism does NOT apply here: this repo is developed
  through Claude Code sessions (this environment), which already provides a native multi-agent primitive — the
  Agent tool. There is no external CLI to wire up.

# ============================================================================
# THE MECHANISM — Claude Code's own Agent tool, no external process
# ============================================================================

Every CHEK role is a call to the Agent tool with `subagent_type="general-purpose"` and an explicit `model` param
(never omit `model` — an omitted model silently inherits the orchestrator's, which breaks the cost routing
TOKEN_ECONOMY.md and CHEK_PROTOCOL.md rely on).

ROLE -> HOW IT IS INVOKED (CHEK_PROTOCOL.md owns the exact prompt bodies for each):
  Step 4b web researcher -> Agent(model="sonnet", prompt = agents/web-researcher.md body + question brief)
  Step 5  fleet planner   -> Agent(model="opus",   prompt = the Step 5 planner prompt, inline in CHEK_PROTOCOL.md)
  Step 6  domain checkers -> Agent(model="sonnet") x N, launched IN ONE MESSAGE — this is the one place true
                              parallelism matters: N independent read-only agents with no shared state.
  Step 8  gap finder      -> Agent(model="sonnet", the Step 8 prompt)
  Step 9  fixer           -> Agent(model="sonnet", the Step 9 prompt) — editing role, run once, then `git status`
  Step 10 critic A + B    -> Agent(model="opus") x 2, launched IN ONE MESSAGE, different prompts
  Step 11 verifier        -> Agent(model="sonnet"), scoped to that round
  Step 12 test writer     -> Agent(model="sonnet") — editing role, tests only
  Step 13 commit+push     -> THE HUMAN. Never an agent, in this mechanism or any other.

WHY THIS IS SIMPLER THAN THE CURSOR-CLI PATH: no proxy, no console-encoding workaround, no PowerShell BOM issue, no
  plan-mode-swallows-the-answer failure mode — all of that existed to make an EXTERNAL headless CLI reliable from
  inside a wrapper script. The Agent tool already returns a clean final report per call; there is nothing to parse
  out of mixed stdout.

WHAT STILL APPLIES, unchanged from the sibling projects' experience:
  - retries are not idempotent for an EDITING role (fixer, test-writer): if a call fails partway after it has
    already written files, do not blindly retry — `git status` first, then decide whether to continue or revert.
  - read-only roles (checkers, critics, verifier, planner, gap-finder, web-researcher) may be retried freely; they
    make no filesystem changes to lose.
  - the fleet is never collapsed into one agent to save calls — CHEK_PROTOCOL.md forbids it explicitly, because one
    agent auditing everything skims and finds nothing. This is the single most important invariant to preserve
    when a mechanism changes.

# ============================================================================
# FALLBACK — if the Agent tool is ever unavailable in a session
# ============================================================================

Run CHEK Steps 1-4 by hand (registry, tests, deploy/runtime state, grep sweeps) — they need no agents and already
catch a real share of problems. Step 4b without agents = a minimal manual web pass only if triggers fired (still
write `## Web research`). Steps 5-12 wait until the Agent tool is available again. Do NOT collapse the fleet into
manual single-pass reading to "get it done anyway" — a skim by the top model alone is exactly what CHEK_PROTOCOL.md
forbids, whatever the mechanism.

# ============================================================================
# WHAT THIS FILE DOES NOT CHANGE
# ============================================================================

- the human still triggers the commit at Step 13; no agent ever runs `git commit`
- read-only roles stay read-only; a critic or checker that edits is a protocol violation regardless of mechanism
- the fleet is never collapsed into one agent to save calls
- findings still land in chek_open.md / chek_never.md / chek_later.md, a problem in exactly ONE of them
- the Step 12 stash check still gates "resolved" — an agent's opinion alone is never evidence
