# AI_COMMANDS — single registry of user command triggers (LLM-ONLY, English on purpose)

PURPOSE: ONE file that defines every user trigger word, what it does, and which file holds its body/protocol.
AUTHORITY: this file is the ONLY definition of triggers. If any other file (CLAUDE.md, memory, .claude/commands/*,
  a file header) states a trigger differently, THIS file wins — then go fix the other file. Never duplicate a
  definition here into prose elsewhere; elsewhere may only POINT here.
FORMAT: flat `key: value`. No tables, no decoration. Read top to bottom; each `--- command:` block is self-contained.

MATCH RULE (applies to every trigger below):
  A trigger fires only when the user's message is ESSENTIALLY JUST the trigger — the bare word, any case, optionally
  with filler («го», «давай», «выполни», «плз», "go"). A trigger word inside a larger instruction is NOT a trigger.
  Ambiguous between two commands below → pick the SAFER one (show/report over execute/deploy) and say so in one line.

LANGUAGE RULE: all AI-facing docs and command bodies are English. Bot UI text, code comments and runtime logs stay
  Russian. Chat with the user: Russian.

AI_FILES (LLM-only, English, flat, no tables):
  AI_COMMANDS.md (this) · CLAUDE.md · AGENTS.md · PROJECT_MEMORY.md · CHEK_PROTOCOL.md · LAST_PROMPT.md ·
  STATE_LOG.md · TROUBLESHOOTING.md · BRANCHING.md · DELEGATION.md · TOKEN_ECONOMY.md ·
  chek_open.md · chek_never.md · chek_later.md
HUMAN_FILES (do NOT read at session start as command authority): README.md · docs/*.md
NEW_FILE RULE: a new root *.md is AI-only by default. Human-readable ONLY if the user says «для человека» OR it
  lives under docs/ as product documentation.

# ============================================================================
# COMMANDS
# ============================================================================

--- command: PROMPT_RUN
trigger: `промпт` | `promt` | `prompt` | `промт` | `/prompt`  (bare, any case, optional filler «го»/«давай»/«выполни»/"go")
action: read LAST_PROMPT.md and EXECUTE its body as if the user had typed that text themselves. Accept it as the
  active task and act. Do NOT ask "which prompt", do NOT re-paste the body as an answer, do NOT ask for confirmation
  first.
body-file: LAST_PROMPT.md (single canonical store of the last AI-authored prompt)
empty-case: file has only the header and no body → tell the user there is no stored prompt.

--- command: PROMPT_SHOW
trigger: `покажи промпт` | `покажи последний промпт` | `что в промпте` | `/prompt show`
action: PRINT the body of LAST_PROMPT.md into the reply so the user can read/copy/judge it. Change nothing.

--- command: PROMPT_WRITE
trigger: `сделай/дай/напиши промпт ...` (a request to AUTHOR a prompt), without the words «для человека»
action: author the prompt LLM-only (English, flat, with `# EXPLANATION:` lines stating WHY each block exists), then
  OVERWRITE LAST_PROMPT.md with it (keep the file's header block), then show it.
invariant: exactly ONE stored "last prompt" exists at any time.
with «для человека»: write it human-readable instead and do NOT overwrite LAST_PROMPT.md.

--- command: CHEK
trigger: `ЧЕК` | `chek` | `чек` (any case), optionally followed by a scope (file / module / topic). No scope = whole
  project — or the project the user names, if the target is a project this bot manages rather than this repo.
variant: `ЧЕК всё` — ignore the suppression registries (chek_never.md + chek_later.md) and re-audit everything.
variant: `ЧЕК` followed by the user's own list of bugs — skip protocol steps 1-8, start at step 9 (fixer).
action: execute CHEK_PROTOCOL.md steps 1-13 verbatim, in order, no skipping "for brevity".
body-file: CHEK_PROTOCOL.md (synced from the AI-kit structure repo — see CLAUDE.md AI-KIT section)
registry-files: chek_open.md (unresolved, with pass counters) · chek_never.md (won't-fix forever) · chek_later.md
  (deferred, suppressed but reminded). A problem lives in exactly ONE of them.
exception: if the conversation is about EDITING the CHEK command itself — do not start an audit.
code-rule: any code written under CHEK follows the minimal-code ladder in CLAUDE.md.

--- command: CHEK_REVIEW
trigger: `review` | `пересмотр` | «пересмотри» sent after a CHEK run
action: re-walk every change made in that run through the minimal-code ladder — dead code? existing helper? stdlib?
  one line? — and simplify whatever fails it. Quality pass only, not a new bug hunt.

--- command: AI_KIT_SYNC
trigger: `синк структуры` | `sync ai-kit` | `обнови структуру` (explicit ask), or implicitly at the CLAUDE.md AI-KIT
  "WHEN" moments (before ending a session that touched a TIER A file)
action: pull the structure repo, diff every TIER A file in `tools/ai_kit.json` against this project's copy, apply
  only real drift not covered by a `local-override` marker, commit with a minimal factual message. Structure repo
  wins by default (see CLAUDE.md AI-KIT). NEVER a blind overwrite; NEVER touch a file with no real diff.
scope-note: CHEK_PROTOCOL.md is included in this sync (it is TIER A and continuously maintained upstream) — but
  publishing a structure-repo change onward to the public chek-protocol repo is a SEPARATE, always-manual step; do
  not conflate the two directions.

--- command: HANDOVER
trigger: implicit — fires when the session is ending or looks likely to end. Explicit form: `хендовер` | `handover`.
action: 1) append a `[HANDOVER]` entry to STATE_LOG.md: date+time MSK, what was done, current state, STOPPING POINT,
  open questions; 2) make sure bugs are in chek_open.md (deferred → chek_later.md), implemented work is in the
  PROJECT_MEMORY.md session log, the current plan is in LAST_PROMPT.md; 3) commit+push anything important that is
  uncommitted (or state explicitly that it was NOT done); 4) leave the next session zero guessing: options +
  recommendation for every decision that waits on the human; 5) give the user a short handover in Russian.

--- command: DM_USER
trigger: implicit — any time there is something the absent user should know, or the user asks to be notified.
action: send the message to the admin(s) through the bot itself once the Telegram layer exists (README "Админка").
  NOT YET WIRED: this repo has no bot runtime yet — until it does, this command degrades to a chat reply plus a
  STATE_LOG `[DM]` entry, and the gap is stated explicitly rather than silently skipped.
log: append `[DM]` to STATE_LOG.md (to whom / what / delivered-in-chat-only vs bot).

# ============================================================================
# STANDING RULES THAT APPLY TO EVERY COMMAND
# ============================================================================

timestamps: every work record (memory, registries, STATE_LOG, session log) carries date AND time as
  `YYYY-MM-DD HH:MM МСК`. Server `date` returns UTC; MSK = UTC+3.
try-and-log: when chasing "make it work properly", try MULTIPLE approaches. Every approach that FAILED gets
  appended to STATE_LOG.md as `[TRIED]` (what, why, observed failure), so no future session burns time on the same
  dead end. Check existing [TRIED] entries before retrying an idea.
merge-policy: after every confidently-correct fix, land it on `main` (definition of "confident" and the branch/
  prefix model: BRANCHING.md).
session-start: read PROJECT_MEMORY.md, then this file. Never read README.md at session start unless the task is
  product scope (this repo currently uses README as its spec — read it when the task needs product intent).
