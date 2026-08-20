# chek_never — CHEK registry: PERMANENTLY won't-fix (LLM-ONLY, English on purpose)

Problems that must NEVER be fixed, until the human explicitly says otherwise: not a bug (false positive), a
deliberate pattern, "that's a feature", or genuinely unsolvable.
CHEK Step 1 reads this file and does NOT report these findings — forever.

Three CHEK registry files; a problem lives in exactly ONE (the invariant is checked at Step 1):
  chek_open.md  — unresolved / in progress (with pass counters)
  chek_never.md — this file (suppressed forever)
  chek_later.md — deferred (suppressed, but reminded)

identity: `file::symbol::pattern` — NEVER a line number (it drifts).
Step 1 cross-checks against the code: the symbol is gone or heavily changed since `added` -> the entry is stale (GC:
  drop it, or return it to chek_open for a re-check).
`ЧЕК всё` ignores this file entirely (full re-check).
This file is committed together with the code when the user asks to commit.
severity: CRITICAL | HIGH | MEDIUM
protocol: CHEK_PROTOCOL.md · trigger: AI_COMMANDS.md

entry format:
```
- id: path/file.py::symbol::pattern
  severity: CRITICAL|HIGH|MEDIUM
  reason: why it is not a bug / why it will never be fixed
  added: YYYY-MM-DD
```

# --- entries below ---

(none yet)
