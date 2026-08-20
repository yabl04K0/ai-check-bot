# chek_later — CHEK registry: DEFERRED (LLM-ONLY, English on purpose)

Suppressed but reminded when nearby code changes (CHEK Step 1 later reminder).
A problem lives in exactly ONE of: chek_open.md · chek_never.md · chek_later.md
identity: `file::symbol::pattern`
include deferred_sha when known.

entry format:
```
- id: path/file.py::symbol::pattern
  severity: CRITICAL|HIGH|MEDIUM
  deferred_reason: why this waits
  first_seen: YYYY-MM-DD HH:MM МСК
  deferred_sha: <short sha, when known>
  remind_when: <the condition that should bring it back>
```

# === later ===

(none yet)
