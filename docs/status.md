# Status

Updated: 2026-09-01 (mainline iteration on baseline `bf29314`). Fields stay
separated: **current fact**, **verification evidence**, **remaining target**,
**blockers**. Nothing counts as verified because a test exists; each row names the
observation behind it.

Environment: Windows 11, Python 3.12, PostgreSQL 16 + pgvector in this project's
own container `rt-claude-postgres` on `127.0.0.1:55447`.

---

## 1. The three-stage contract

**Current fact.** Retrieval and diagnosis are now three separately gated stages
with their own pass conditions, rejection reasons and trace:

```text
retrieved_candidate  ->  accepted_same_incident  ->  actionable_incident
```

`incident.matched` can only be set by stage 2. Candidates and their rejection
reasons appear only in `--debug`. No `upgrade`/`downgrade`/`migrate`/
`config_change`/`workaround` can leave stage 2. The verifier can withdraw the
whole incident, not just a citation (`revoke_incident`).

**Verification evidence.** `stages.stopped_at` in every response; the five common
cases stop at `actionable_incident`, `accepted_same_incident`,
`accepted_same_incident`, `accepted_same_incident` and `retrieved_candidate`
respectively. `tests/test_cli_contract.py` and `tests/test_eval_suite.py` assert
the boundaries through the installed CLI.

**Remaining target.** A dense/RRF candidate channel, admitted only behind its own
calibrated acceptance gate.

**Blockers.** None; deliberately not started.

---

## 2. Same-incident identity (the 3/10 false candidates)

**Current fact.** Identity requires agreement across independent feature classes
(`error`, `structural`, `behavior`, `component`), never topic overlap. Three rules
decide, all measured:

* a **stated root cause** is decisive - if the reporter says CSP, YAML, DNS, auth,
  disk, port, TLS, permission, OOM, module-resolution or version-conflict, a
  candidate that does not exhibit that mechanism is a different incident
  (`different_root_cause`);
* **subjects disagree by role, and the role comes from context** - being scoped
  proves nothing about a package. `@acme/theme-kit crashes` and
  `@acme/app depends on @acme/theme-kit` are opposite facts about the same
  token, so every package mention keeps its span and the cue that classified it:

  | role | how it is decided | authority |
  |---|---|---|
  | primary package | the report says it failed: `X crashes`, `X did not load`, `X: crashes`, `X stopped working` | conflict is decisive |
  | referenced dependency | the report says it is used and says nothing bad about it | weakens a match, never refuses one |
  | confirmed non-primary | the report says it is fine: `X is healthy`, `X does not crash` | weakens a match; if it is the *only* kind named, nothing may act |
  | **conflicted subject** | the report contradicts itself: `X is healthy but crashes` | **never authorises an action** |
  | **unresolved subject** | named, role undetermined - **the default** | **refuses a match the candidate cannot account for** |

  Roles are read in one order, and it matters: predicates on *both* sides of a
  mention outrank a dependency cue, because `failed to install X` and
  `could not import X` are reports about X, not statements that X is used. A
  bare `name@version` is only a dependency when the sentence says it is
  installed or used; otherwise it stays unresolved like anything else we cannot
  classify;
  | source path | `loader/src/internal.ts` | vetoes only when primary packages do not already agree |
  | runtime builtin | `node:path` | can neither prove nor refuse |
  | module name | `theme-parser`, with corpus/syntax/morphology evidence | weakens a match, never refuses one |

  Only a primary-package overlap can fire `primary_package_plus_second_class`,
  and only primary packages are consulted for the veto - so a shared dependency
  can never cancel a conflict between two blamed packages, and a query that
  names only dependencies cannot veto anything at all;

* **relation and state are separate facts** - `@dsh is a healthy dependency`
  says two things: it is used, and it is fine. A single mutually-exclusive role
  could only keep one, and it kept the relation, so the health fact never
  reached the gate and an explicitly cleared package still authorised an
  upgrade. A mention now carries `relation` (dependency / direct / unknown) and
  `state` (failing / healthy / conflicted / unknown) independently, and the
  displayed role is derived from the pair;

* **facts are aggregated per package, not per mention** - `@x is healthy.` in
  one sentence and `@x crashes.` in another are two statements about one
  package, so they merge into a contradiction. Aggregation is by canonical name,
  which covers repeats, later sentences and sub-path aliases
  (`@x/inner.js` folds into `@x`);

* **condition claims are bound, wherever they live** - a report's claims about
  condition (`it crashes`, `this package is healthy`, `the server fell over`)
  are extracted across the whole text, not inside each package's own window,
  because a claim in the *next sentence* is still about the package. Each claim
  binds to a package, to another named subject, or to nothing:

  | claim | binding |
  |---|---|
  | `@x crashes` | explicit |
  | `We import @x! It crashes.` | anaphoric - one antecedent, so it binds |
  | `We use @a and @b. It crashes.` | **unresolved** - two antecedents, never guessed |
  | `the server crashes` | other subject - bound elsewhere, not our business |
  | `imports the healthy @x` | explicit to `@x` - an adjective describes the noun it precedes, not the clause's subject |

  While an unresolved **failure** claim is outstanding, a dependency plus a
  shared path, module or symbol may not authorise an action: those say where
  code lives, not what failed. An unresolved *health* claim does not block,
  because it cannot cause a wrong upgrade;

* **an anaphor after a dependency is not ignored** - `import X, but it crashes`,
  `use X; the package fails` and `installed X@1.2.3, then it hangs` attribute the
  failure to X. A failure whose subject cannot be resolved, or a negation whose
  target cannot be interpreted (`could not import X`), leaves the package
  *ambiguous* rather than filed as a safe dependency;

* **two authorizations sit above every identity rule** - not inside them, so
  no rule can route around them:

  1. *exculpation* - reads the **state fact**, so a package called healthy
     counts even when it is also called a dependency. When a report names
     packages, says all of them are fine, and never names a culprit, **nothing**
     may act on it: not a shared package,
     not a shared path, not a shared module. The shared source path was the
     tempting one, and it was the leak: the file really is the same, but the
     report had already said the package that owns it is healthy. A report that
     names no package at all (a pasted log) is deliberately exempt, so
     snippet-only incidents still match on their rare symbols;
  2. *contradiction* - `X is healthy but crashes` says something is wrong and
     that it is not. It cannot establish what failed even when the candidate
     names the very same package;

* **the default is fail-closed** - this is the invariant that matters, because
  every earlier round of this gate leaked the same way: a phrasing the cue
  vocabulary did not recognise made the package a *neutral mention*, a neutral
  mention could not refuse anything, and a familiar stack path was enough to
  match a real incident and recommend a version change. Adding the missing
  phrase fixed that phrasing and left the next one open.

  A package whose role cannot be determined is now `unresolved_subject`, and
  when the query carries one the candidate never names - with only a dependency,
  a path or a symbol linking them - the match is refused. None of those say
  *what* failed. The guard does not depend on recognising the phrasing, so a
  twelfth unseen wording is refused for the same reason as the eleven known
  ones. Contradictory predicates (`X is healthy but crashes`) resolve to
  `unresolved` too, rather than to whichever cue happened to be checked first;

* **negation names what it negates, and the answer depends on what that is** -
  a bare `does not` is not evidence of anything. Three cases, resolved before any
  bare health cue is considered:

  | sentence | negated thing | verdict |
  |---|---|---|
  | `X does not crash` | a failure verb | health - X is not the subject |
  | `X did not load` | an expected action | failure - X is primary |
  | `X is not working` | a positive state | failure - X is primary |

  The third case is why `working`, `healthy`, `stable`, `up to date` and the rest
  are a *separate* vocabulary from failure verbs: reading `not working` as
  `working` inverted the answer;

* **a cue speaks only for its own mention** - cues are matched anchored, and a
  mention's window stops at a sentence break or at another package mention.
  Within that window the *predicate chain* is followed, so coordinated and
  label-style syntax reaches the same verdict as the plain form:
  `X starts but crashes`, `X loads, then crashes`, `X: crashes`,
  `X, which crashes`, `X stopped working`, `X won't start`. Each predicate is
  matched anchored at its own position, so `X is healthy but the server crashes`
  is not read as X crashing - that clause has a different subject;

* **a predicate outranks a dependency cue** - `peer dependency @scope/lib is not
  up to date` reports a failure *of* `@scope/lib`. Treating it as a mere
  dependency because of the leading cue hid the blame;

* **an exculpation is not a match** - when every package a report names is one
  it says is fine, and it names no culprit, a shared exception type and symbol
  are topical similarity. A report that simply names nothing (a pasted snippet)
  is unaffected: its rare symbols still carry identity;

* **a blamed package the candidate never names is a disagreement** - a shared
  source path is not enough when the report points at a package the candidate
  does not discuss at all. `@nebula/theme-engine is not working` plus a familiar
  stack path is a report about Nebula;

  The same reasoning fixed a cause signal: `peer dependency ... is healthy` no
  longer counts as `version_conflict`; a conflict word has to be present;

* **packages that ship inside one another are not a conflict** - the product
  family is read from the repository's own `package.json` manifests at sync
  time (275 of them here), and strictness differs by purpose:

  | | acceptance (`related`) | retrieval (`related_for_retrieval`) |
  |---|---|---|
  | name ancestry required | yes | yes |
  | manifest evidence | **both** names published here | either name published here |

  So `@scope/dsh` and `@scope/dsh-client-modules` can carry a match, while
  `@scope/dsh-fabricated` - a name nothing publishes - can only help *find* a
  candidate and can never establish identity. A shared scope is not a relation
  at all. No package name appears in the codebase.

  The family also reaches **stage 1**: related packages expand the candidate
  query, family-related candidates sort first, and they are exempt from the
  `MAX_IDENTITY_CHECKS` budget, so a product-family incident cannot be silently
  truncated before it is ever evaluated;
* **environment can only veto** - runtime/OS never contributes to identity.

**Verification evidence.** Committed suite: 35 negative and regression cases,
0 false incidents, 0 unsafe actions. The three reported leaks (CSP blocking
`client.js`, `cordis.yml` duplicate key, npm DNS failure) are rejected as
`different_root_cause` and asserted in `tests/test_identity_gate.py`.
`evals/cases/regressions.yaml` adds 13 **developer-authored** wordings - seven
subject-conflict cases where a different module hits the same `e.indexOf` symbol
and the same `TypeError`, three adversarial cases that pair a foreign scoped
package or path with the incident's own vocabulary (including one where both
sides name `client-modules`), and three wordings that previously leaked a
candidate. All 13 require `matched=false` and forbid upgrade/downgrade/migrate/
config_change/workaround.

`tests/test_subject_strength.py` pins the properties at the unit level:

* **role precedence** - a foreign package stays foreign when the query also
  carries the candidate's `node:path`, its `node:module`, its source path, its
  module name, its `__DSH_BOOT__` symbol and its `TypeError`, all at once, and
  also when it *imports the candidate's own package*;
* **weak roles cannot veto** - a module-name mismatch is refused as
  `insufficient_identity_evidence`, never as a subject veto;
* **transformation invariance** - padding a correct report with business
  adjectives, business nouns, quoted runbook text, tracker tags
  (`[SEV-2] ... TICKET-4821`) or a healthy scoped dependency
  (`also depends on @sindresorhus/is`) must not change the verdict, the rule, or
  the shared packages, and must not rescue an unrelated report either.

`tests/test_package_roles.py` covers the roles themselves: what crashes is
primary, what is used is a dependency even when scoped, both roles in one
sentence, spans and cues preserved, and the manifest-driven product relation.

`tests/test_cue_endtoend.py` re-checks the same semantics **through the real
interfaces** rather than by calling the gate: the installed console script as a
subprocess and the MCP server over the protocol, asserting they agree. Its MCP
half connects to the server object in-process, which exercises the protocol but
not the installed binary - `tests/test_cue_scope.py` covers that gap by
launching `repo-troubleshooter-mcp` as a real stdio subprocess through
`StdioServerParameters`, and checks row counts across all 12 business tables
before and after that session. It covers
bare negation, negated failure verbs, negated expected actions, health cues,
healthy peer dependencies, and that every family-related candidate that reached
stage 1 was actually evaluated.

These regression wordings were written by the same developer who wrote the code,
so they prove the defects are closed - **not** that the system generalises. No
independent holdout result is claimed here; hidden evaluation is run separately
by the evaluator.

**Remaining target.** Duplicate-vs-same-incident distinction. Measured over 109
threads, 3 queries matched a *different* thread; all three were reviewed by hand
and are plausible duplicate reports (sandbox escalation, pnpm install, subagent
model inheritance), so they are recorded as duplicates, not errors - but the
system does not yet say "duplicate of" explicitly. Independent hidden evaluation
is also outstanding and is the evaluator's to run.

**Blockers.** None.

---

## 3. Paraphrase recall

**Current fact.** Symptom text is decomposed into feature classes, and each stored
thread's own features are mined into `symptom_signature` rows at sync time. A
query with no stack trace and no symbols can therefore reach an incident through
its **behavioural profile** ("has no entries or batches", "never preloads") plus
component agreement. Aliases are never hand-written: a phrase exists as a
signature only because a real reporter wrote it.

**Verification evidence.** The exact rewrite the independent test found missing -
*"The Harness web page starts on Windows but the client boot graph has no entries
or batches, and the browser never preloads the dsh client JavaScript module"* -
now matches discussion 5084 by `behaviour_profile_plus_component` and still goes
through the version and applicability gates to `upgrade -> dsh-v0.1.2-alpha.2`.
19,185 signatures mined from 505 objects.

**Remaining target.** One documented recall gap, kept in the suite and excluded
from Correct Action@1 rather than deleted: `para-boot-graph-user-voice`, a rewrite
that shares no vocabulary at all ("startup list" for entries/batches, "fetches"
for preloads) and overlaps on exactly one behavioural feature. Closing it needs a
semantic channel; lowering the identity threshold instead would trade one recall
for false matches.

**Blockers.** None.

---

## 4. Measured results

**Current fact.** Committed suite: 69 cases - 10 incidents across 10 subsystems,
6 paraphrases, 25 negatives, 13 developer-authored regressions, 15 perturbations.
**68 pass, 1 is a documented recall gap** (`para-boot-graph-user-voice`).

History of this denominator, so the number is not read as growth in quality:
**54/55** before the regression cases, **64/65** after the first 10, **68/69**
since three adversarial cases and one modifier-invariance case were added. The
subject-role rework changed no case outcome: still 68/69, same gap.

**Correct Action@1 exclusion rule:** the documented gap is excluded from the
denominator (n=29, not 30). It is not counted as a pass and not counted as a
failure - it is reported separately as `documented_recall_gaps`. Nothing else is
excluded.

| Metric | Value | n |
|---|---|---|
| Correct Action@1 | 1.00 | **30** (gap excluded) |
| negative false-incident rate | 0.00 | 38 |
| unsafe action rate (negatives + regressions) | 0.00 | 38 |
| unsafe action rate (environment contradictions) | 0.00 | 2 |
| version/release verdict accuracy | 1.00 | 15 |
| citation validity | 1.00 | 81 ids |
| claim-support validity (structural) | 1.00 | 69 |
| abstention recall | 1.00 | 38 |
| **abstention precision** | **0.75** | 51 |
| documented recall gaps | 1 | - |
| future-leakage violations | 0 | - |
| latency p50 / p95 / max | 57 ms / 793 ms / 870 ms | 69 |

**Verification evidence.** `python evals/runner.py` writes
`evals/reports/latest.json`; the hard gates are re-asserted in
`tests/test_eval_suite.py::TestHardGates`.

**Remaining target.** Abstention precision is the weakest number here: 0.75 now,
0.68 before the cue fixes, 0.58 on the original 55-case suite. It is honest but crude - it counts every
`collect_more_info` as an abstention, including the *correct* "your version
already contains this change" answers, which are informative results rather than
refusals. A better denominator, and B1-B6 baselines, are still to build. Claim-support validity is **structural only** - every claim
cites listed, resolvable evidence; nothing yet proves the excerpt entails the
sentence.

**Blockers.** B1-B6 need the alternative retrieval modes, which are not built.

---

## 5. Engineering contracts

**Current fact.** `mypy --strict` over `src` is clean (49 files, 0 errors) with no
blanket `ignore_errors` and no unexplained `type: ignore`; the only overrides are
`ignore_missing_imports` for third-party packages that ship no stubs (`mcp`,
`alembic`, `pgvector`). An MCP server (`repo-troubleshooter-mcp`) exposes exactly
two tools over stdio, sharing the CLI's engine and contract. Both are declared
read-only in the protocol (`read_only_hint=True`, `destructive_hint=False`,
`open_world_hint=False`) and `diagnose` calls the engine with `persist=False`, so
a tool call cannot write even the derived incident record the CLI may cache. A
database that is down, empty, foreign or stale fails within ~3 seconds with a
command to run, never a traceback; MCP returns a structured error instead of
hanging.

**Verification evidence.** `uv run mypy src` → success; `uv run pytest` → **354
passed**, measured. The suite has grown each round - 118, 146, 157, 178, 188,
216, 263, 291, 320, now 354 with the claim-binding tests - so a number quoted from an
earlier round no longer matches.
`tests/test_mcp_roundtrip.py`
drives a real MCP SDK client: lists tools, calls both, asserts CLI/MCP parity on
status, action, target, `incident.matched`, `stages.stopped_at` and the
evidence-id set, checks the read-only annotations over the wire, and counts all
11 business tables before and after protocol calls - including a matched,
actionable diagnosis - requiring them identical. Dead-database run measured at
3 s, exit 1, no traceback. `repo-troubleshooter-mcp --help` answers and exits
instead of starting a session (it previously hung - found by the delivery gate).

**Remaining target.** Structured errors for partial-sync states inside MCP tool
payloads beyond the current coverage note.

**Blockers.** None.

---

## 6. Data

**Current fact.** 550 discussions, 1510 comments, 123 doc files, 7 releases,
275 package manifests, **15,663 stored symptom signatures** (measured after the
rebuild at extractor version 8; earlier rounds reported inflated figures - see
the row-accounting note below). Discussion coverage is still partial and reports `degraded`.
A paced backfill (`rt sync … --backfill-pages N`) walks older history a few pages
at a time, persists its GraphQL cursor, resumes where it stopped, and never claims
`complete` while pages remain.

**Verification evidence.** Backfill measured across three runs: discussions
402 → 550, comments 1219 → 1510. Signature counts from those runs are **not**
comparable: they summed both mining passes. `discussions_backfill`
stayed `degraded` with `exhausted: false` throughout. `rt status` shows per-source
health and `data_as_of`.

**Remaining target.** Full backfill of all 5167 discussions, and a structurally
different second repository onboarded by profile alone.

**Blockers.** GraphQL point budget - a full walk cannot be done in one run, by
design. The second repository has not been started.

---

## 7. Delivery

**Current fact.** Local gates all pass: `uv sync --extra dev --frozen`,
`ruff check .`, `ruff format --check .`, `mypy src`, `pytest` (118 tests),
`evals/runner.py`, `docker compose up -d`, `db init`, `db ping`, `status`,
`uv build`, and a clean-venv install of the built wheel running
`repo-troubleshooter --help`, `diagnose --help`, `repo-troubleshooter-mcp --help`
and `--check`.

**Verification evidence.** Commands and outputs are listed in the iteration report,
each checked by exit code rather than by reading the tail of the output.

**Remaining target.** A green CI run on a pushed commit, and an independent
hidden evaluation run by the evaluator - no such result is claimed here.

**Blockers.** `blocker: no remote configured` - `git remote -v` is empty, and
pushing is the user's decision. The workflow (lint, format, strict mypy, unit
tests, fresh migration, database tests, packaging + MCP smoke) is committed but
has never executed anywhere.

---

---

## 8. Signature freshness

**Current fact.** Feature extraction is versioned (`FEATURE_EXTRACTOR_VERSION`).
The version is stamped on the mined rows, and `require_fresh_signatures` runs
before every diagnosis: if the stored signatures were mined by a different
extractor, or none exist, the engine **raises instead of answering**. Stored
candidate features and live query features have to come from the same extractor
or every comparison between them is meaningless, and a confident wrong answer is
worse than a refusal.

The migration that introduced typed subjects deletes every mined row and clears
the recorded version, so an upgraded database is *forced* through a rebuild
rather than silently diagnosing against stale features.

**Verification evidence.** After `db init` on the existing database, `diagnose`
refused with `no symptom signatures are stored ... build them with:
repo-troubleshooter signatures <repo>` and exited non-zero; after
`rt signatures <repo> --rebuild` (500 objects, 32,882 rows, extractor_version 3)
it answered again. `tests/test_signature_freshness.py` rewinds the stored version
and asserts the refusal on all three paths - engine (raises), CLI (non-zero exit,
no traceback, `--rebuild` in the message) and MCP (`signatures_stale` structured
error) - then restores it.

**Remaining target.** The rebuild is manual. A sync that notices a version
mismatch could rebuild automatically.

**Blockers.** None.

---

## 9. Counting what is actually stored

**Current fact.** Mining runs twice - once on what each thread proves alone, once
more after the corpus can vouch for module names - and most rows offered in the
second pass already exist. Adding both passes together reported roughly twice
what the database holds, so the counts are now separate:

| count | meaning | last rebuild |
|---|---|---|
| `rows_attempted` | (kind, value) pairs offered to the database | 31,314 |
| `rows_inserted` | of those, actually new | 15,689 |
| `rows_stored_total` | rows the repository holds afterwards | **15,689** |

Earlier rounds reported the attempted figure (32,882, and 19,185 before that) as
if it were storage. It was not.

By kind, after the cue-scope rebuild: subject_package **33**;
subject_dependency 173; subject_mentioned 821; cause 70; the remaining kinds are
unchanged.
That last number is the point of the rework: a package counts as the subject
only where a report actually blames it, so package identity is rare and precise
rather than granted by token shape. It rose from 18 to 36 when negated expected
actions (`did not preload X`) were recognised as failures, and `cause` fell from
75 to 70 as healthy peer dependencies stopped counting as version conflicts.

**Verification evidence.** `rt signatures <repo> --rebuild` prints all three, and
`rows_inserted` comes from `RETURNING` on the insert rather than from the length
of the batch, so conflicts are excluded rather than assumed.

**Remaining target.** The second pass re-offers every row; it could re-offer only
the objects whose module names the corpus newly vouches for.

**Blockers.** None.

---

## 10. A defect the end-to-end tests found

**Current fact.** When a report gave no runtime at all, the applicability gate
compared `None` against the incident's runtime bounds, could not order it, and
reported `unresolved_version` - blaming the user's *core version*, which was
perfectly parseable, for a gap in their report. The correct answer is that the
runtime is missing information: the bounds simply cannot be checked.

**Verification evidence.** Found by `tests/test_cue_endtoend.py`, which runs the
real symptom through the installed CLI without `--runtime`: it returned
`collect_more_info` with the rationale "core version '0.1.2-alpha.1' cannot be
ordered against release versions". It now returns `upgrade -> dsh-v0.1.2-alpha.2`
and records the unchecked bounds as a reason. This was invisible to every prior
round because the existing cases all supplied a runtime.

**Remaining target.** None; the missing-runtime path is now a stated reason
rather than a verdict about the version.

**Blockers.** None.

---

## 11. Three cue-scope defects, found by review

All three produced a *confident wrong answer* rather than an abstention, which
makes them the worst class of defect this product can have.

| input | was | now |
|---|---|---|
| `@nebula/theme-engine is not working`, importing a healthy DSH dependency, with a familiar boot symptom | matched #5084, recommended `upgrade -> dsh-v0.1.2-alpha.2` | no match, no version action |
| `@deepseek-ai/dsh-client-modules is healthy; @nebula/theme-engine crashes...` | both packages became `mentioned`, matched #5084, upgraded | DSH `mentioned`, Nebula `primary`, no match |
| `peer dependency @scope/lib is not up to date` | cue `healthy: up to date` | no health cue |

**Root causes, all structural rather than vocabulary:**

1. the health vocabulary had no negation guard, so `not working` matched
   `working`;
2. health cues were found with an unanchored search over a fixed character
   window, so one package's cue reached the next mention;
3. the health check ran *before* the negation check, so it won when it should
   not have.

A fourth surfaced while fixing them: with the roles corrected, the surprise
cases still matched through `source_path_plus_second_class`, because sharing a
stack path was enough even though the report blamed a package the candidate
never mentions. That is now its own refusal.

**Verification evidence.** `tests/test_cue_scope.py`: 28 tests covering the rule
directly, then every surprise case through the installed CLI subprocess *and* a
freshly launched `repo-troubleshooter-mcp` stdio process, asserting the two
agree and that no business table changed across the MCP session. The case that
should still match - the real boot-graph symptom - is asserted on both surfaces
in the same file, so the fix cannot be a blanket refusal.

**Remaining target.** The clause splitter is punctuation and conjunction based.
A report written as one long clause can still put two packages in one window.

**Blockers.** None.

---

## 12. The identity default, and why it had to change

**Current fact.** Four rounds of review found four different phrasings that
matched a real incident and recommended a version change when they should have
abstained. Each was fixed by teaching the cue vocabulary a new phrase, and each
fix left the next phrasing open, because the *default* was unsafe: anything the
vocabulary did not recognise became a neutral mention, and a neutral mention
could not refuse anything.

The default is now `unresolved_subject`, and the gate refuses when the query
carries an unresolved package the candidate never names and the only links are a
dependency, a path or a symbol.

**Verification evidence.** `tests/test_failclosed_identity.py` runs eleven
phrasings of the same external-package report - each carrying the real
incident's behaviour, stack path, symbol and exception - through the installed
CLI subprocess *and* a freshly launched `repo-troubleshooter-mcp` stdio process:

| phrasing | | phrasing | |
|---|---|---|---|
| `X is not working` | ✓ | `X went sideways on us` | ✓ |
| `@dsh is healthy; X crashes` | ✓ | `X is healthy but crashes` | ✓ |
| `peer dependency X is not up to date` | ✓ | `healthy DSH + its own source path` | ✓ |
| `X starts but crashes` | ✓ | `failed to install X` + healthy DSH dep | ✓ |
| `X loads, then crashes` | ✓ | `could not import X` | ✓ |
| `X: crashes` | ✓ | `cannot require X` | ✓ |
| `X, which crashes` | ✓ | `@dsh is healthy but crashes` | ✓ |
| `X stopped working` | ✓ | `nebula-theme@1.2.3 went sideways` | ✓ |
| `X won't start` | ✓ | `@dsh is healthy but it crashes` | ✓ |

All eighteen return `matched=false`, `action=abstain`, `target=null` on both
surfaces, the surfaces agree, and no unsafe action appears anywhere in the set.
A twelfth, deliberately unseen wording is asserted to be refused too - by the
invariant rather than by a phrase.

The same file asserts the real boot-graph symptom still matches and still
recommends `dsh-v0.1.2-alpha.2` on both surfaces, so the guard cannot be
satisfied by refusing everything.

**Remaining target.** The predicate chain is punctuation and conjunction based.
A single clause with two subjects and no coordinator can still be misread; that
would show up as `unresolved`, which is the safe direction.

**Blockers.** None.

---

## 13. Three authorization paths, found by review

The fail-closed roles from the previous round were necessary and not sufficient.
Review found three routes that still reached `upgrade -> dsh-v0.1.2-alpha.2`,
all through `source_path_plus_second_class`:

| report | why it got through |
|---|---|
| `@deepseek-ai/dsh-client-modules is healthy and does not fail. The host process crashes separately.` + the incident's own path | the exculpation guard only covered three weak rules; the path rule was not one of them |
| `failed to install @nebula/theme-engine` (and `could not import`, `cannot require`) | the dependency cue was checked before the failure stated in front of the mention, so the blamed package became a plain dependency |
| `@deepseek-ai/dsh-client-modules is healthy but crashes` | correctly `unresolved`, but the candidate names the same package, so the unresolved guard did not fire |

**Fixes, as authorizations rather than more vocabulary.** Two checks now run
*above* every identity rule, so no rule can route around them: exculpation and
contradiction. Role reading was reordered so predicates on both sides of a
mention outrank the dependency cue, and `name@version` no longer becomes a
dependency without an install context - which had contradicted the fail-closed
default in the previous round.

**Verification evidence.** The three reported reports now return
`matched=false, action=abstain, target=null` through the installed CLI, and all
eighteen phrasings pass through the CLI *and* a freshly launched
`repo-troubleshooter-mcp` stdio process with the surfaces agreeing. The real
boot-graph symptom still matches and still recommends `dsh-v0.1.2-alpha.2` on
both surfaces.

**Remaining target.** Contradiction is detected per mention. A report that
contradicts itself across two sentences about the same package is not yet
merged into one verdict.

**Blockers.** None.

---

## 14. Fact aggregation, and the last of the role collapses

**Current fact.** Review found four more reports that reached
`upgrade -> dsh-v0.1.2-alpha.2`. All four came from one modelling error: role was
a single mutually-exclusive enum, but a report states two independent things
about a package - how it is connected, and what condition it is in.

| report | what was lost |
|---|---|
| `@dsh is a healthy dependency. The host crashes separately.` | `dependency + healthy` collapsed to `dependency`; the health fact never reached the exculpation check |
| `@dsh is healthy.` … `@dsh crashes.` | two mentions of one package landed in two different sets and were never merged |
| `We import @nebula/theme-engine, but it crashes` | the anaphor was ignored, so the blamed package stayed a plain dependency |
| `installed nebula-theme@1.2.3, then it hangs` | same, plus the version's dots truncated the predicate window |

**Verification evidence.** All four now return `matched=false, action=abstain,
target=null` through the installed CLI. The public regression set grew from 18 to
24 phrasings, each run through the CLI *and* a freshly launched
`repo-troubleshooter-mcp` stdio process with the surfaces agreeing; the real
boot-graph symptom still matches and still recommends `dsh-v0.1.2-alpha.2`.
`uv run pytest` → 320 passed.

**Remaining target.** Aggregation merges states per canonical package name.
Two packages that are genuinely different but normalise to the same name would
merge too; no such case is known here, and the direction of the error is safe
(more contradictions, fewer actions).

**Blockers.** None.

---

## 15. Claims that live in another sentence

**Current fact.** An independent run of 12 unseen negatives returned 9 wrong
upgrades. One cause, three symptoms:

| report | what went wrong |
|---|---|
| `We import @nebula/theme-engine! It crashes` | the predicate window stopped at `!`, so `It crashes` never reached the package and it stayed a harmless dependency |
| the same with a newline instead of `!` | identical |
| `this package is healthy` | `this` alone was treated as a coreference, leaving `package is healthy` to be parsed as nothing, so the health fact vanished |

Condition claims are now extracted across the whole report and bound
individually - to a package, to another named subject, or to nothing. A pronoun
binds only when the antecedent is unique. An unresolved *failure* claim blocks
any match that rests on a dependency, path, module or symbol.

**Verification evidence.** All seven representative reports return
`matched=false, action=abstain, target=null` through the installed CLI. The
public set is now 32 phrasings, each through the CLI and a freshly launched
`repo-troubleshooter-mcp` stdio process; the real boot-graph symptom still
matches and still recommends `dsh-v0.1.2-alpha.2`. `uv run pytest` → 354 passed.

Two narrower defects were found and fixed while doing it: the health-word regex
had no word boundaries, so `fine` matched inside `undefined` and `ok` inside
`maxTokens`, turning source snippets into health claims; and
`SCOPED_PACKAGE_RE` swallowed a trailing sentence period, so
`@scope/pkg.` read as a different package.

**Remaining target.** Binding is clause- and pronoun-based. A claim whose
subject is a noun phrase that *paraphrases* a package (`the theme engine
crashes`) is treated as another subject, not as that package.

**Blockers.** None. This round's public set is again developer-authored;
independent hidden evaluation is the evaluator's to run, and the previous
round's `unsafe action = 0` claim was true only of the committed set - the same
caveat applies here.

## Not built

Dense retrieval / RRF, B1-B6 baselines, second repository, full backfill,
duplicate-of relations, claim entailment. From spec §22: GraphRAG, Neo4j,
multi-agent, long-term memory, web UI, automated GitHub replies, whole-codebase
embedding and automatic "which commit introduced the bug" remain deliberately
unbuilt.
