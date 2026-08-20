# chek_open — CHEK registry: UNRESOLVED / in progress (LLM-ONLY, English on purpose)

Real problems that are not closed yet. Two counters per entry:
  passes_run  — fix<->verify rounds in the CURRENT run
  passes_life — total rounds over this problem's whole life
A resolved+verified problem is DELETED from here: its permanent record is the git commit + the regression test.

Three CHEK registry files; a problem lives in exactly ONE: chek_open.md · chek_never.md · chek_later.md
identity: `file::symbol::pattern` — NEVER a line number.
severity: CRITICAL | HIGH | MEDIUM
protocol: CHEK_PROTOCOL.md · trigger: AI_COMMANDS.md

entry format:
```
- id: path/file.py::symbol::pattern
  severity: CRITICAL|HIGH|MEDIUM
  status: open|escalated
  passes_run: N
  passes_life: N
  first_seen: YYYY-MM-DD HH:MM МСК
  attempts:
    - what was tried -> what the reviewer found
  subfindings:
    - detail
```

# === open problems ===

(none — no CHEK run has executed against this repo yet; there is no code to audit)
