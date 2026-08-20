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
CONTEXT: doc-porting session (branch claude/md-structure-bot-setup-p1gcn7) also produced two sibling repos:
  yabl04K0/0000 (private ai-dev-kit structure repo, source of truth for the TIER A doc stack) and yabl04K0/1111
  (public CHEK-protocol-only mirror). Both are populated and pushed to `main`. yabl04K0/1111 still needs its
  GitHub visibility flipped to public by hand — no API path from this environment can do it (write access to
  GitHub is blocked through this session's proxy for anything outside the allowed MCP tool calls).

READ FIRST: PROJECT_MEMORY.md -> AI_COMMANDS.md -> latest STATE_LOG [HANDOVER].

## GOAL — implement the bot feature backlog the user described across this session, in this rough priority order

1. AI-account "health probe" / API smoke test: for each connected AIProvider account, on a configurable schedule
   (UP TO 5 RUNS PER DAY, user-configurable count and time-of-day for each run — not just an interval), open a
   fresh conversation/session against that provider, send a minimal probe prompt, capture that the response came
   back, then immediately delete/close that conversation. Purpose is purely connectivity/quota verification, not a
   real task. Message text sent to the provider should also be configurable (not hardcoded). Route through the
   AIProvider abstraction — never a direct SDK call (see CLAUDE.md "CRITICAL: provider abstraction").
2. Live "in-flight" menu for a running task (CHEK run or any task type): show which agents are currently active
   and how many, updating as the fleet progresses (mirrors the README "Прогресс" single-editable-message pattern —
   reuse that, do not invent a parallel status UI).
3. Mid-task interaction: let the user send the AI a message WHILE a task is executing (not just before/after), and
   a stop/cancel control that actually halts the in-progress task cleanly (not just hides the message).
4. Multi-account pooling per AI provider: support inserting more than one account for the SAME provider (e.g. two
   Claude accounts) and have the bot use them seamlessly — likely quota-aware round robin/fallback, extending the
   existing "Fallback-цепочка при недоступности провайдера" idea in README to also cover same-provider multi-
   account, not just cross-provider fallback.
5. Per-account proxy (Xray) configuration: each AI-provider ACCOUNT (not a single global proxy) should be able to
   carry its own proxy settings, entered by the user, and the bot should route that account's traffic through it.
   Look at how MeCelium wires TELEGRAM_PROXY/COLLECTOR_EGRESS_PROXY via Xray for the connection-layer pattern, but
   the NEW requirement here is per-account granularity, which MeCelium's global proxy does not have — do not just
   copy MeCelium's single-proxy-per-bot model.
6. UX pass: before or alongside building the above, read through MeCelium's and AutoPost's actual bot UI code
   (inline-keyboard menu structure, settings screens, admin panel) for patterns worth reusing in ai-check-bot's own
   menu — the user explicitly asked to "check the convenience of my other projects" and reuse what's good, not
   design ai-check-bot's UX from a blank page. sd-forge-bot's simpler settings/admin flow may also be worth a look
   given it's a smaller, more approachable bot.

## HARD RULES (from CLAUDE.md, restated because this is a bot-building task, not a doc task)
  NEVER call anthropic/OpenAI/Cursor-CLI/local-LLM SDKs directly from a handler — always through AIProvider.
  NEVER let any delegated task run `git commit`/`git push` on its own; human confirms, including for this repo.
  NEVER add a code path capable of deleting a repo or rewriting its history.
  Repo has NO code yet as of this handover — item 1 likely requires standing up the AIProvider interface itself
  first (at least a minimal version) before a scheduler can call it.

# EXPLANATION: the user dropped these requirements incrementally across a long session focused on porting the
# AI-collaboration doc stack (CLAUDE.md/CHEK_PROTOCOL.md/etc.) into this repo and into two new sibling repos. They
# said explicitly to finish the doc-porting/repo-creation work first ("для начала сделай то что просил раньше")
# and treat the items above as the next task. Nothing here has been implemented yet — this file exists so the next
# session does not have to re-mine it out of chat history.
