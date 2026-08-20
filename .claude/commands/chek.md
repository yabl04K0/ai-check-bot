---
description: Глубокий аудит кода с реестром проблем (ЧЕК)
---

Invoke the CHEK command (code audit). Its trigger definition lives in `AI_COMMANDS.md` (command CHEK); the protocol
body lives in `CHEK_PROTOCOL.md`.

Action: read `CHEK_PROTOCOL.md` and execute steps 1-13 verbatim, in order. Fix nothing before Step 9. Commit nothing
before Step 13 (the human triggers the commit). Do not skip a step "for brevity". Do not audit the project yourself
instead of running the fleet.

$ARGUMENTS handling:
- empty -> audit the whole project.
- `всё` / `all` -> ignore the suppression registries (chek_never.md + chek_later.md) and re-check everything.
- a file/module/topic -> that is the audit scope.
- a list of bugs from the user -> skip steps 1-8, start at Step 9 using that list as the report.
