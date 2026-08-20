# BRANCHING — branch model, promotion gates, prefixes (LLM-ONLY, English on purpose)

PURPOSE: define WHERE a change lives at each level of confidence, and WHAT must be true before it moves up.
WHY: without levels, every commit goes straight to `main` and `main` is simultaneously "verified truth" and
  "untested experiment" — a regression is then found only by a user noticing something broke.
FORMAT: flat `key: value`. No tables.
AUTHORITY: this file defines the branch model. Command triggers live in AI_COMMANDS.md.

# ============================================================================
# THE THREE LONG-LIVED BRANCHES (stability increases upward)
# ============================================================================

branch: main
  meaning: PRODUCTION TRUTH. Only changes that are verified or trivially provable. Assume the user may deploy any
    commit here at any moment, once a deploy target exists.
  invariant: the project test command is green on every commit (see CLAUDE.md "Commands" once code exists).
  invariant: never rewrite history (no force-push, no rebase of pushed commits) once `main` has other readers.
  who-writes: a confident fix lands here immediately (see GATE-CONFIDENT below). Anything else arrives by merge
    from `beta`.

branch: beta
  meaning: WHAT IS INTENDED FOR SOAK / staging (or next deploy) once a deploy target exists.
  use: risky or unverifiable-by-tests work goes here, soaks, then merges to main.
  invariant: tests green; `beta` always contains `main` (merge `main` into `beta` before promoting soak results).

branch: alpha
  meaning: INTEGRATION / UNPROVEN. Work in progress, large refactors, spikes, anything that may not survive review.
  freedom: may be broken between commits, may have red tests INSIDE a work session — but never merges upward while
    red.
  who-writes: topic branches merge here when they are too big or too coupled to promote directly.

promotion-direction: `prefix/topic` -> `alpha` -> `beta` -> `main`. Fixes never travel downward as merges; instead,
  after `main` moves, merge `main` into `beta` and `alpha` to keep them ahead of it.

CURRENT STATE: this repo has no deployed target yet — until one exists, `beta`/`alpha` exist mainly for large or
  unproven work; small confident changes still go straight to `main` under GATE-CONFIDENT below.

# ============================================================================
# SHORT-LIVED BRANCH PREFIXES (kebab-case, English, delete after merge)
# ============================================================================

fix/     — bug fix with a known root cause.            example: fix/scheduler-double-fire
feat/    — new user-visible capability.                example: feat/provider-quota-router
hotfix/  — production is broken NOW; smallest possible change; may go straight to `main` + immediate deploy.
chek/    — a batch of fixes produced by one CHEK run.  example: chek/20260820-md-structure
test/    — tests only, no production code change.      example: test/mock-provider-clients
docs/    — AI-docs / registries only, no code.          example: docs/ai-kit-sync
chore/   — infra, deps, CI, .gitignore.                 example: chore/ci-lint
exp/     — experiment that is expected to be thrown away; never merges to `main` directly.

naming: `<prefix>/<short-kebab-what>`; add a date only for CHEK batches (`chek/YYYYMMDD-topic`).
lifetime: a topic branch that cannot merge within one session is a sign the change is too big — split it.
cleanup: delete the branch locally and on origin after its merge. Stale `claude/*` branches on origin are leftovers
  from cloud sessions; they are not part of this model and may be deleted once clearly merged or abandoned.

# ============================================================================
# GATE-CONFIDENT — when a fix may go STRAIGHT to main
# ============================================================================

A fix is "confidently correct" and lands on `main` immediately (commit, merge, push) when ALL of these hold:
  1. ROOT CAUSE named. Not "the symptom stopped" — you can state the mechanism in one sentence.
  2. SCOPE is only what the finding named. No opportunistic refactor riding along.
  3. The project test command is green.
  4. PROOF, one of:
     a. a regression test that is RED before the fix and GREEN after (`git stash` check) — strongest, default; or
     b. the change is trivially provable by reading (typo, a missing check, a wrong constant), i.e. no behavior can
        plausibly depend on the old form; or
     c. verified on the live target for behavior no unit test can reach (network, an external API) — record the
        observation in STATE_LOG.md.
  5. CRITIC PASS done: re-read every changed file top to bottom for regressions, contract breaks and CLAUDE.md
     violations, and say in the chat what you checked.
If ANY of 1-5 fails -> it is NOT confident: it goes to `alpha` (or `beta` for live soak), and only reaches `main`
  after the missing evidence exists.

# ============================================================================
# GATE-SOAK — beta -> main
# ============================================================================

conditions: deployed or running as the soak target; tests green; observed through at least one full cycle of the
  behavior it touches; no new error class attributable to it; STATE_LOG.md has a `[BETA]` entry describing the
  soak and its outcome.
soak-length: default ~24h of real traffic for scheduled/background paths; a single verified execution is enough for
  isolated UI/handler changes.
failure: soak found a problem -> fix on `beta` (or revert on `beta`) and restart the soak. Never promote "it
  probably settled".

# ============================================================================
# MECHANICS
# ============================================================================

confident fix, no topic branch needed:
  git checkout main
  <edit> ; pytest -q
  git add <specific files> ; git commit ; git push

risky work:
  git checkout -b fix/<topic> beta        # or alpha for unproven work
  <edit> ; pytest -q ; git commit
  git checkout beta ; git merge --no-ff fix/<topic> ; git push origin beta
  <deploy; verify live; STATE_LOG [DEPLOY]/[BETA]>
  git checkout main ; git merge beta ; git push        # only after GATE-SOAK
  git branch -d fix/<topic> ; git push origin --delete fix/<topic>

keep the levels ahead of main after a direct main commit:
  git checkout beta ; git merge main ; git push origin beta
  git checkout alpha ; git merge beta ; git push origin alpha

merge style: `--no-ff` when merging a topic branch (keeps the batch visible as one unit); plain fast-forward is
  fine for `main -> beta -> alpha` catch-up merges.
commit message: describe WHAT was fixed and WHY, in Russian (project language for history). Never a process
  message like "CHEK audit of N files" — history must explain substance, not method. End with the Co-Authored-By
  line, per this session's own git-commit convention.
