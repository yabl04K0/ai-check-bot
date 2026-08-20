---
description: Выполнить/показать сохранённый промпт (LAST_PROMPT.md)
---

Invoke the PROMPT command. Its single definition lives in `AI_COMMANDS.md` (commands PROMPT_RUN / PROMPT_SHOW /
PROMPT_WRITE) — read that file's PROMPT blocks and follow them exactly. Do not improvise different behavior here.

Default for a bare `/prompt` (no arguments): PROMPT_RUN — read `LAST_PROMPT.md` and EXECUTE its body as if the user
had typed it. Accept it as the active task and act: no "which prompt?", no re-pasting the body instead of doing it,
no asking for confirmation first.

$ARGUMENTS handling:
- `show` / `покажи` -> PROMPT_SHOW: print the body without executing anything.
- anything else -> a refinement of how to execute (e.g. "только шаг 2").
- `LAST_PROMPT.md` has only its header and no body -> say there is no stored prompt.
