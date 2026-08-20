# TROUBLESHOOTING — connectivity/provider incident journal + diagnostics (LLM-ONLY, English on purpose)

PURPOSE: one place for network/API/egress symptoms and their fixes, so a future session does not re-diagnose a
  known issue from scratch. Read on any connectivity symptom (provider API unreachable, Telegram polling dies,
  GitHub API auth failing).
FORMAT: LLM only, flat text, `key: value`, no tables.
EMPTY TODAY: this repo has no runtime yet, so there are no incidents to record. Expected future categories, based
  on the architecture in README.md:
  - Telegram Bot API reachability (api.telegram.org may be blocked in some hosting regions — the sibling projects
    route through a proxy; decide per deploy target when a target exists, do not assume one is needed).
  - Each AIProvider backend's own auth/quota failure modes (Anthropic API errors, OpenAI/Codex auth expiry, Cursor
    CLI session expiry, local Ollama/vLLM endpoint down).
  - GitHub fine-grained token scope/expiry (Contents rw + Administration for visibility only — see README
    "GitHub-интеграция").

# === entries below (append; newest last) ===
