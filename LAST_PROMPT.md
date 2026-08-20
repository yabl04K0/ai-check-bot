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

(empty — no prompt stored yet)
