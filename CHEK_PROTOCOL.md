# CHEK_PROTOCOL — the ЧЕК audit protocol, steps 1-13 + Step 4b smart web research (LLM-ONLY, English on purpose)

SOURCE: this file is synced from the private AI-kit structure repo (see CLAUDE.md AI-KIT section) and is the most
  actively iterated file in the kit — treat it as continuously current upstream, not "set once".
TRIGGER: defined in AI_COMMANDS.md (`ЧЕК` / `ЧЕК всё` / `ЧЕК <scope>` / `ЧЕК` + user's bug list). This file is the
  ONLY copy of the protocol body for this project — memory holds a pointer, never a second copy.
CONTRACT: fix nothing before Step 9. Commit nothing before Step 13 (the human starts the commit). Skip no step
  "for brevity" (includes Step 4b when its triggers fire — do not "save tokens" by skipping web research).
PORTABILITY: the protocol must work on ANY project. Anything project-specific (a language, a domain invariant, a
  concrete domain split) is a REFERENCE EXAMPLE, not a hardcoded list — Step 5 re-derives the fleet per project. This
  repo has no production code yet, so the examples below are illustrative until Step 5 runs for real here.
FORMAT: flat text, `key: value`, no tables. Agent prompt templates are quoted blocks meant to be pasted verbatim.

ROLES (deliberately separated — never merge them into one agent):
  web-researcher (Step 4b) — internet only: search/fetch/analyze outside sources; read-only on repo docs; never edit.
  checkers (Steps 6, 8) — find only, read-only, never edit.
  fixer (Steps 9, 11) — edits only; does not review itself, does not commit, does not mask symptoms.
  critics (Step 10 and the FINAL round of Step 11) — TWO of them with DIFFERENT focuses (A: regressions/diff,
    B: root-vs-symptom/contracts), read-only, never edit, never commit.
  verifier (intermediate rounds of Step 11) — ONE, scoped to that round's problems, read-only.
  test-writer (Step 12) — writes regression tests only, never touches production code.
  commit+push (Step 13) — the HUMAN triggers it, never an agent.

MODELS (`model=` param of the Agent tool; the orchestrator keeps its own model):
  sonnet — web-researcher (4b), fleet checkers (6), gap-finder (8), fixer (9 and 11), intermediate verifier (11),
    test-writer (12).
  opus   — fleet planner (5) and both critics (10, and the final round of 11).
  reason: the bulk of agents is cheap on sonnet and good enough at finding; the planner needs architectural judgment;
    the judge that releases a diff to the human must not be weaker than the fixer. Web research is a SEPARATE role
    (agents/web-researcher.md) so external-truth work is not smuggled into the orchestrator or into code checkers.
  HONEST about decorrelation: opus and sonnet share a pretrain, so swapping model size buys WEAK decorrelation, not
    an independent view. The real decorrelation axis is the PROMPT axis — critic A vs critic B having sharp,
    non-overlapping focuses; web-researcher vs checkers is another axis (outside vs inside). NEVER rest quality on
    "opus and sonnet will catch different things" alone. The only LLM-judgment-free anchor is the Step 12 stash check.
  NEVER put checkers on a weak/small model — finding quality IS the product of CHEK.
  NEVER put web-researcher on a weak/small model either — shallow search theatre is worse than a declared light skip.

FORBIDDEN:
  - auditing the project yourself instead of running the fleet (Steps 5-6)
  - a read-partial "explore"-style agent for auditing — unusable for audit, it skims
  - delegating Step 2 or Step 4 to agents (the orchestrator does them itself)
  - doing Step 4b yourself in the orchestrator when triggers fired — MUST run the web-researcher agent
  - skipping Step 4b when any of its triggers fired, or doing a single lazy search and calling it "research"
  - collapsing the fleet into one agent ("I'll run one agent to cover everything")
  - merging web-researcher into a domain checker or gap-finder "to save a call"
  - the planner leaving a source file outside every domain, hunting bugs instead of designing the fleet, or
    trimming domains below full project coverage
  - the orchestrator hardcoding domains instead of running the planner
  - critics or the verifier editing code (two agents editing the same files conflict)
  - the fixer masking a symptom to silence a reviewer: empty/widened `except`, deleting or weakening tests
  - any agent running `git commit`
  - silently losing an escalated problem — it must end in never/later or stay in chek_open

# ============================================================================
# STEP 1 — problem registry: load, cross-check, GC (orchestrator; Read/Glob/Grep)
# ============================================================================

Three registry files in the project root; a problem lives in exactly ONE:
  chek_open.md  — unresolved / in progress (counters passes_run, passes_life)
  chek_never.md — permanently won't-fix (suppressed forever)
  chek_later.md — deferred (suppressed, but reminded)
Missing file = treat as empty (Step 13 creates it). Each file explains its own format in its header.
SEVERITY WORDS: registry files use English `CRITICAL|HIGH|MEDIUM`; agent output and the chat report to the user use
  `КРИТИЧНО|ВЫСОКИЙ|СРЕДНИЙ` (the user reads the report). Map 1:1 when writing an entry to the registry.

Read all three, then:
1. INVARIANT: every id (`file::symbol::pattern`) appears in at most ONE file. A duplicate across files means a
   broken move from a previous Step 13 → report it, keep the entry in the STRONGEST file (never > later > open),
   remove it from the others.
2. GC vs code: for each never/later entry, check whether the symbol still exists (Glob+Read/Grep). Symbol gone or
   heavily changed → the entry is stale: drop it from never; for later, surface it in item 4.
3. SUPPRESSION BLOCK: build one block from never+later and append it to the END of EVERY fleet checker prompt
   (Step 6) and the gap-finder prompt (Step 8):
     ALREADY SETTLED — do NOT report these findings (human marked never / deferred later):
     [never + later entries: id + reason]
4. LATER REMINDER (loud trigger): for each later entry compare `deferred_sha` with the symbol's current state.
   Code AROUND the symbol changed → raise it to the orchestrator as "deferred X became relevant — revisit?".
   Suppression is NOT lifted automatically; the human decides at Step 13.
5. CARRY OVER open entries: load chek_open records as "known open" — they enter the working report at Step 7 with
   `passes_life` incremented. They are NEVER suppressed.

`ЧЕК всё` — ignore suppression (never + later) entirely: full re-check from scratch.
Finding identity = `file::symbol::pattern`. NEVER a line number (it drifts).

# ============================================================================
# STEP 2 — tests (orchestrator; Bash)
# ============================================================================

Run the PROJECT's test command. Do not hardcode it — derive it from the project:
  pytest.ini / pyproject.toml[tool.pytest] / a tests/ dir → `pytest -q`
  package.json with a "test" script → `npm test`
  Cargo.toml → `cargo test`; go.mod → `go test ./...`; Makefile with a test target → `make test`
  otherwise: look in README/docs for the test command
No tests found → note "no tests" in the report and continue.
Record: N passed / N failed. Continue regardless of the result.

# ============================================================================
# STEP 3 — deploy/runtime state (only if a deploy target and access exist)
# ============================================================================

If the project has a deployed target (server, container, managed host) and access is documented, connect and pull
logs/state. Collect: the newest log, the deployed commit SHA if recorded, local HEAD. Compare: local HEAD vs
deployed SHA vs what actually RUNS. Look for ERROR/CRITICAL, repeated WARNINGs, tracebacks the process survived.
Put findings under `## Deploy / runtime state` in the final report. No deploy target yet, or access unavailable →
skip the step and say so.

# ============================================================================
# STEP 4 — sweeps for known footguns (orchestrator; Grep)
# ============================================================================

A sweep = a fast project-wide Grep for CLASSES of bug known to bite THIS project/language. Patterns are not
hardcoded — assemble them:
1. From project docs (CLAUDE.md / PROJECT_MEMORY.md "known problems", "invariants") pull this project's footguns.
2. Add language generics for the language found in Step 2 (once code exists): e.g. Python — bare `except:`;
   JS/TS — `==`, `as any`; anywhere — hardcoded secrets/tokens, TODO/FIXME/HACK.
3. Grep each pattern; keep the results for Step 7.
This repo has no production code yet — Step 4 is a no-op until there is something to sweep; note that explicitly
rather than fabricating hits.

# ============================================================================
# STEP 4b — SMART WEB RESEARCH (web-researcher agent, model=sonnet; orchestrator wires it)
# ============================================================================

Purpose: CHEK must not be blind to the outside world. Local grep finds what is already in the tree; the web finds
  current best practice, broken upstream URLs, CVE/advisory patterns, library API changes, and known-inefficient
  designs the repo has not yet absorbed. This step is a DEDICATED ROLE (`agents/web-researcher.md`), not the
  orchestrator "quickly searching" and not a domain checker with a side quest.
WHEN IT FIRES (any one is enough — then run the web-researcher for real, not one lazy query):
  1. NEED — the audit touches a topic the orchestrator does not already know cold from project docs (a new provider
     SDK, a Telegram API change, a GitHub API scope, a scheduling library, a dependency major version, ...).
  2. INEFFICIENCY — the AI judges a local design/algorithm/IO pattern wasteful, outdated, or likely wrong compared
     to common practice, and needs external confirmation or a better known approach.
  3. UNCERTAINTY — Steps 2-4 left an open question docs in-repo cannot settle.
  4. USER / SCOPE — the CHEK scope itself is a research topic.
WHEN IT MAY STAY LIGHT: pure typo/constant fixes with zero external dependency — orchestrator writes
  `web: skipped, reason=…` in the report and does NOT spawn the agent. NEVER skip silently when triggers 1-4 fired.
HOW:
  1. Orchestrator lists 3-10 concrete search questions from THIS project's domains plus any inefficiency claims.
  2. Orchestrator runs ONE agent: subagent_type="general-purpose", model="sonnet", body = agents/web-researcher.md
     + the question brief. The agent file defines a HARD LOOP: decompose → multi-query search → diversity check →
     WebFetch primary sources → gap-driven rewrite → cite. Caps: ~18 searches / ~10 fetches / ~4 refine rounds.
  3. Agent returns a `## Web research` brief. Orchestrator keeps it for Steps 5-8. Findings that are real bugs go
     into the working report at Step 7 with identity `file::symbol::pattern` when a local symbol exists; pure
     "industry tip" with no local hook goes to the human summary, not fake chek_open spam.
  4. Feed the brief into the Step 5 planner prompt and Step 8 gap-finder prompt so the fleet is not designed in a
     vacuum.
HARD RULES: do not paste secrets from .env into queries. Do not treat a random blog as proof over the project's own
  CLAUDE.md invariants. Web research does NOT authorize edits before Step 9.

# ============================================================================
# STEP 5 — fleet planner (orchestrator → ONE agent, model=opus)
# ============================================================================

Purpose: make CHEK PORTABLE. Static domains are tuned to one project and meaningless on another. The planner splits
THIS project into domains itself: it decides the NUMBER of checkers and writes each one's prompt so that together
they cover the whole project and none is overloaded.
Run ONE agent: subagent_type="general-purpose", model="opus". The planner does NOT hunt bugs and does NOT edit.

Planner prompt:
```
You are an audit architect. Design a fleet of read-only checker agents (each will run model=sonnet) that TOGETHER
cover the ENTIRE project with none of them overloaded. You do NOT hunt bugs and do NOT edit — you only design the fleet.

Preparation:
1. Glob("**/*") -> project map. Identify the language(s) and the REAL sources; exclude .git, venv/node_modules,
   binaries, assets, generated and vendored code.
2. Estimate each source file's size in lines.
3. If present, Read the project docs (CLAUDE.md, PROJECT_MEMORY.md, ARCHITECTURE/README): extract subsystem
   boundaries and PROJECT INVARIANTS (rules that are easy to violate — they become the focus of the prompts).
4. If the orchestrator attached a Step 4b ## Web research brief — USE it: bias domain prompts toward upstream
   changes, known-bad patterns, and inefficiency hunts named there.

Design the split honoring these invariants STRICTLY in priority order:
1. COMPLETENESS (top): every source file belongs to at least ONE domain. Nothing without an auditor.
2. COHESION: a domain is a coherent subsystem so the agent sees the interactions inside it. Do NOT split a
   tightly coupled module across agents.
3. BALANCE: no domain exceeds the BUDGET of ~10 files OR ~2500 lines. Bigger -> split into sub-domains. Goal:
   every agent can read ALL of its files IN FULL and thoughtfully.
Conflict resolution: COMPLETENESS > COHESION > BALANCE.

The number of domain agents N is whatever the invariants require (small project 2-3, large 8-12+).

For EACH domain output: domain name + exact file list + load estimate, and a TARGETED sonnet checker prompt: what
this subsystem is, which bug classes are likely, and which project invariants apply. Do NOT repeat the standard
rules (Step 6 appends those).

ALWAYS add one CONTRACT domain (cross-cutting): the SEAMS between domains (return types != expected, non-existent
keys, unhandled enum values, signature drift). Project-agnostic, always exists.

Output (a SPECIFICATION, not findings):
DOMAIN <name> [<N files>, <M lines>]: file1, file2, ...
PROMPT:
<the full sonnet checker prompt>
---
(repeat per domain; then the contract domain)
SUMMARY: N domain + 1 contract. Files covered: X of Y. Not covered: <list or "none">.
```
Then the orchestrator checks the SUMMARY "covered X of Y". Uncovered sources → sent back to the planner or appended
to the closest domain. Final safety net is the mechanical coverage check in Step 7.

# ============================================================================
# STEP 6 — fleet checkers per the Step 5 specification (orchestrator; Agent)
# ============================================================================

MANDATORY: launch ALL agents from the fleet spec IN ONE MESSAGE — N domain + 1 contract.
Type for each: subagent_type="general-purpose", model="sonnet". No merging agents. No trimming below the planner's
spec. All checkers are read-only — they find, they do not fix.

Common rules appended to EVERY checker prompt:
1. Read the project docs in full if they exist (CLAUDE.md, PROJECT_MEMORY.md). No docs → skip, rely on the
   planner's prompt.
2. Read the files of YOUR domain from the list in your prompt; use Glob only to avoid missing a file.
3. Read each file COMPLETELY — not partially, not via grep.
4. Look for real bugs only: logic, behavior, crash, data loss, duplicates.
5. Do NOT report: style, dead code, missing comments.
6. Do NOT fix anything — findings only (fixes happen at Step 9).
7. Output in Russian, one line per finding: `КРИТИЧНО/ВЫСОКИЙ/СРЕДНИЙ file:line — что не так — что сломается`.
8. Only what you are sure of after reading the real code.
9. (orchestrator appends) the "ALREADY SETTLED" suppression block from Step 1 — do not report those.
10. Your LAST output line lists the files you actually read: `Прочитано: file1, file2, ...`.

# ============================================================================
# STEP 7 — aggregation (orchestrator)
# ============================================================================

Wait for the whole fleet (N domain + contract).
COVERAGE CHECK (before aggregating — mandatory): `Glob("**/*")` over project sources → collect the `Прочитано:`
lines from every fleet agent → compute the difference. Any source read by NO checker (except clearly irrelevant
ones) is read by the ORCHESTRATOR ITSELF and checked against the same patterns, or marked "not covered by any
checker — checked by the orchestrator". The final report must contain no uncovered areas.

Merge Steps 2 and 4 results. Deduplicate. Sort by severity; on conflict take the highest. Merge with carried-over
open entries from Step 1 item 5 by id: already-known → `passes_life += 1`; new → `passes_life = 1`.

Report shape:
```
## КРИТИЧНО
file:line — problem — what will break

## ВЫСОКИЙ
...

## СРЕДНИЙ
...

## Контракты между модулями
[contract-agent findings]

## Deploy / runtime state
[real ERROR/WARNING/tracebacks, or "no deploy target" / "unavailable"]

## Тесты
pytest: N passed / N failed
[names of failures]

---
Итого: N КРИТИЧНО · N ВЫСОКИЙ · N СРЕДНИЙ · Тесты N/N
```
Do not include LOW or STALE-DOCS; the user will ask separately if wanted.

# ============================================================================
# STEP 8 — gap-finder (orchestrator; ONE agent, model=sonnet)
# ============================================================================

After aggregation run ONE subagent_type="general-purpose", model="sonnet", handing it the FULL Step 7 report.

```
You audit this project's code. You are given an aggregated audit report. Your task is to find what the fleet checkers
MISSED. Do not fix. Do not repeat anything already in the report.

Preparation: Read the project docs in full (CLAUDE.md, PROJECT_MEMORY.md).
If a Step 4b ## Web research brief is attached — use it as Angle 5: hunt local code that contradicts upstream
facts or still implements the inefficiency the brief named. Do not repeat the brief as findings; only NEW local bugs.

Audit report (already found — do NOT repeat):
[PASTE THE FULL STEP 7 REPORT]
[OPTIONAL: PASTE STEP 4b WEB RESEARCH BRIEF]

Search strategy — four angles (plus Angle 5 when a web brief exists):
Angle 1 — code adjacent to existing findings: for each finding, read its whole file and look for the SAME pattern
  in neighboring functions.
Angle 2 — async / concurrency edge cases: how does the code behave if a background job raises mid-way? Are
  exceptions swallowed anywhere?
Angle 3 — state after an error: an except that neither re-raises nor logs; an operation that fails mid-way and
  leaves state inconsistent.
Angle 4 — bug classes ABSENT from the docs (anti-monoculture): every fleet checker and the orchestrator read the
  SAME project docs and are tuned to ONE list of known pains — that is a collective blind spot. Look OUTSIDE the
  documented patterns: unclosed file descriptors / HTTP sessions / DB cursors; division by zero; races on a FILE
  (not just memory); encoding/locale problems; unbounded growth of long-lived dicts; timezone comparisons; an ID
  that is str where int was expected.

Output in Russian — ONLY new bugs absent from the report:
КРИТИЧНО/ВЫСОКИЙ/СРЕДНИЙ file:line — what is wrong — what will break
If there are none, say so explicitly: "Новых находок нет. Проверено: [list what you checked]"
```

EARLY EXIT: if the final report is EMPTY — stop here. Do NOT run the fixer (9) or the critics (10); Steps 9-13 are
skipped. Tell the user EXACTLY why it is empty:
  - genuinely nothing found → "Аудит чист, исправлять нечего, коммита нет." MANDATORY: attach the gap-finder's
    "Проверено:" list.
  - part/all suppressed by the registry → "Новых находок нет. Подавлено реестром: N never + M later. Запусти
    «ЧЕК всё» чтобы пересмотреть подавленное."
Either way show the deferred footer (Step 13) if non-empty, even on an early exit.

# ============================================================================
# STEP 9 — fixer (orchestrator; ONE agent, model=sonnet)
# ============================================================================

```
You fix real bugs in this project's code. Do not touch style, dead code or comments.

Preparation:
1. Read the project rules in full (CLAUDE.md). They are binding.
2. Read the project memory in full (PROJECT_MEMORY.md).
3. For every bug in the report: Read the whole file BEFORE editing it.

Audit report:
[PASTE THE FULL STEPS 7 + 8 REPORT]

How to work:
- Fix in order: КРИТИЧНО -> ВЫСОКИЙ -> СРЕДНИЙ. Do not skip СРЕДНИЙ.
- Each fix: Read the file in full -> Edit -> next.
- Edit ONLY the functions/lines named in the report. If a fix requires touching adjacent code, first list what and
  why, then edit.
- A bug spanning several files: read them all before editing any.
- Cannot fix without regression risk -> skip it and explain why.
- Obey the project rules while editing (this project: provider abstraction, no repo-delete code paths, human
  confirms commit/push, minimal-code ladder).
- Do NOT git commit and do NOT run tests — the orchestrator does that.

Output in Russian:
file:line — what was fixed
file:line — ПРОПУЩЕНО: reason
Коллатеральные правки (adjacent code, not from the report): file:line — why it had to be touched
```
Then the orchestrator itself runs the project test command and records the result for the critics.

# ============================================================================
# STEP 10 — TWO DIFFERENT critics in parallel (orchestrator; Agent x2, model=opus)
# ============================================================================

MANDATORY: call the Agent tool exactly twice IN ONE MESSAGE, subagent_type="general-purpose", model="opus". They
get DIFFERENT prompts and work independently — that prompt axis IS the main decorrelation of blind spots. Both are
READ-ONLY. Shared context: `git diff HEAD`, the report, the fixer's output, the test result.

Critic A — REGRESSIONS AND DIFF:
```
You are a read-only verifier. Focus: REGRESSIONS AND DIFF. You do NOT edit and do NOT commit.
Preparation: Read the project rules and memory in full; Bash `git diff HEAD`; Read every changed file in full.
The fixer's change list (including its "Коллатеральные правки" block): [PASTE STEP 9 OUTPUT]
Tests after the fixer: [PASTE N passed / N failed]
Check (your angle — did the fixer break what worked):
- regressions in adjacent code inside the same files/functions the fixer touched
- the fixer's collateral edits: justified? do they break neighboring behavior?
- any test that used to pass and now fails — name the cause
- did behavior change on boundary inputs (empty state, None, unreachable resource)?
Output in Russian, report only:
## Подтверждено исправлено
file:line — ок
## Регрессии / задетое смежное
file:line — what is wrong — how to fix
```

Critic B — ROOT VS SYMPTOM AND CONTRACTS:
```
You are a read-only verifier. Focus: ROOT VS SYMPTOM AND CONTRACTS. You do NOT edit and do NOT commit.
Preparation: Read the project rules and memory in full; Bash `git diff HEAD`; Read every changed file in full.
Audit report (what should have been fixed): [PASTE STEPS 7 + 8 REPORT]
The fixer's change list: [PASTE STEP 9 OUTPUT]
Check (your angle — does the fix cure the cause):
- each fix: does it solve the root problem, or mask a symptom?
- new project-rule violations introduced by the edit
- broken module contracts between the parts this project defines
- items the fixer skipped: can they be fixed safely? (say how, but do NOT edit)
Output in Russian, report only:
## Подтверждено исправлено
file:line — ок
## Симптом вместо корня / нарушение правил / порванный контракт
file:line — what is wrong — how to fix
## Не исправлено фиксером
file:line — can it be fixed safely, and how
```

# ============================================================================
# STEP 11 — convergence loop: fix <-> verify until confirmed (orchestrator)
# ============================================================================

Merge both critics' reports and deduplicate. `unresolved` empty -> go straight to Step 12.

```
MAX_PER_PROBLEM = 3        # round cap per problem
MAX_GLOBAL = 3             # round cap for the whole loop (including newly introduced regressions)
round = 1

while unresolved is not empty and round <= MAX_GLOBAL:
    fix(unresolved)                        # ONE follow-up fixer agent, prompt below
    update chek_open: per problem passes_run += 1; append an attempt (what was tried)
    run the project test command

    if round == MAX_GLOBAL or unresolved-after-fix looks empty:
        verify = TWO critics (A + B, Step 10 prompts, model=opus) scoped to this loop's edits
    else:
        verify = ONE scoped verifier (prompt below, model=sonnet)

    new = (what the verifier did NOT confirm) + (regressions / new bugs / masking from this round)

    for p in new:                          # OSCILLATION DETECTOR, per problem
        if p.passes_run > MAX_PER_PROBLEM or the complaint about p is identical to last round:
            p.status = escalated
            remove p from new, keep it in chek_open as escalated

    unresolved = new
    round += 1

resolved  = problems the verifier / final critics confirmed
escalated = whatever remains in unresolved + anything marked escalated
```

Intermediate verifier prompt (ONE agent, sonnet, read-only):
```
You are a scoped read-only verifier. You do NOT edit and do NOT commit.
Verify ONLY the listed problems and this round's edits. Do NOT hunt for new problems outside the list.
Preparation: Read the project rules and memory in full; Bash `git diff HEAD`; Read every touched file in full.
This round's problems + what was already tried: [PASTE unresolved + attempts]
Check:
- each problem: closed ON THE MERITS, or masked (swallowing try/except, widened except, a test deleted/weakened)?
- a changed test: FORCED by the fix, or a workaround? Justify.
- did the follow-up introduce a regression or a NEW bug?
Output in Russian:
## Закрыто по существу
problem — ок
## Не закрыто / маскировка / регрессия / новый баг
problem — what is wrong — how to fix
```

Follow-up fixer prompt (ONE agent, sonnet):
```
You fix code. Reviewers found problems in the previous fixes. Fix them on the merits. Do NOT commit.
Preparation: Read the project rules and memory in full; Read each relevant file IN FULL before editing.
Problems + history of previous attempts (do NOT repeat a failed approach): [PASTE unresolved + attempts]
Rules:
- Fix the ROOT, not the symptom. FORBIDDEN to silence a reviewer: no empty try/except, no widened except, do NOT
  delete or weaken tests.
- Edit only what is named; adjacent edits go in a separate "Коллатеральные правки" block.
- Cannot do it without regression risk -> skip and explain.
Output in Russian:
file:line — what was fixed (how the ROOT was closed)
file:line — ПРОПУЩЕНО: reason
Коллатеральные правки: file:line — why
```

After the loop the final test run must be green (or the failure explained). If `escalated` is non-empty, do NOT
report "all good": those problems go to the human at Step 13 with their full attempt history from chek_open. Not
converging within MAX_GLOBAL is a NORMAL outcome, not a failure.

# ============================================================================
# STEP 12 — regression tests (orchestrator; ONE agent, model=sonnet)
# ============================================================================

After the loop (tests green) run ONE test-writer. Goal: pin EVERY closed problem with a test.

```
You write regression tests for this project. Do not touch production code — tests only.
Preparation: Read the project rules in full; read the project memory (test patterns); read several existing tests
to match their style.
Closed problems (write a test for each): [PASTE THE RESOLVED SET FROM STEP 11]
Rules:
- The test asserts CORRECT behavior with a concrete assert — NOT "no exception raised".
- The test must HIT the buggy condition, not merely pass on current code.
- A bug not coverable by a unit test (needs a live external API/hardware) -> SKIP it and state the reason.
- For each test, say which problem it pins.
Output in Russian:
tests/file.py::test_name — пришпиливает: <problem>
ПРОПУЩЕНО: <problem> — reason
```

Then the orchestrator runs the tests — must be green.

MANDATORY STASH CHECK ("the test really catches the bug") — the only check in all of CHEK that does not depend on
LLM judgment, therefore NOT optional:
  `git stash` the production edits -> run the new tests (expect RED) -> `git stash pop` -> run again (expect GREEN).
  - A test that is GREEN even before the fix is a dud: rewrite it so it fails on pre-fix code. If it cannot be made
    to fail -> masking signal -> the problem is NOT resolved, return it to chek_open.
  - STATUS BINDING (default): a problem becomes resolved ONLY if its regression test passed the stash check.
  - EXCEPTION for bugs genuinely not unit-testable: resolved is allowed WITHOUT a stash test, but only if BOTH final
    opus critics confirmed the fix addresses the ROOT and is not masking. Mark: "resolved без regression-теста:
    <why it is not coverable>".
  - Neither a passing stash test nor both critics confirming the root -> NOT resolved, stays in chek_open.

Resolved problems are DELETED from chek_open at Step 13 — their permanent record is now the test + the commit.

# ============================================================================
# STEP 13 — registry routing + human confirmation + commit&push (orchestrator)
# ============================================================================

The commit is NOT made by an agent. The orchestrator shows the result, updates the registry, and waits for an
explicit "yes".

1. Show the user:
   - totals: N resolved · N escalated (did not converge) · N regression tests added
   - escalated items (if any): what it is, severity, passes_run/passes_life, short attempt history
   - deferred reminder: `Отложено: N` — the quiet footer from chek_later; loudly highlight the ones Step 1 raised
     as "nearby code changed"
   - `git diff --stat` (which files, how many lines)
   - final test result: N passed / N failed
2. Ask verbatim: "Зафиксить и запушить?"
3. REGISTRY ROUTING per the human's decision (a move = FIRST append to the target file, THEN delete from the
   source):
   - resolved -> DELETE from chek_open
   - escalated — ask the human per item: "not a bug / it's a feature / don't touch" -> chek_never; "later, remind
     me" -> chek_later (record `deferred_sha`); "keep fixing" -> stays in chek_open
   - any report item the human called NOT a bug early on -> chek_never with a reason
   - NOTHING escalated is lost: it is in never, or later, or still in chek_open.
   Include the registry files in `git add`.
4. ONLY on explicit confirmation, as ONE operation:
   git add <the specific changed files + chek_open.md chek_never.md chek_later.md>   (never `git add -A`)
   git commit -m "<message>"
   git push
   Branch target and the "confident fix" gate: BRANCHING.md.
Commit message: describe WHAT was fixed in substance and why, in Russian. Never a process message like "аудит ЧЕК N
файлов" — history must explain substance, not method. End with the Co-Authored-By line.
The user says "no"/"stop"/asks for changes -> do not commit; do what they asked, show again, ask again.
