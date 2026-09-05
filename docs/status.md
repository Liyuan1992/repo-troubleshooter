# Status

Updated: 2026-09-04c (workspace context collection after `40a31bb`). Fields stay
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
15,689 signatures mined from 500 objects (the 19,185/505 quoted here for
several rounds was an attempted-row count from a superseded build; see section 9).

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
subject-role rework changed no case outcome: still 68/69, same gap. The
conservative claim rule briefly cost a second case, `preset-mounting-discovery`,
which was registered as a known gap and has since been recovered by fixing the
code test that was misreading it (section 17) - not by loosening the gate.

**Correct Action@1 exclusion rule:** the documented gap is excluded from the
denominator (n=30, not 31). It is not counted as a pass and not counted as a
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
| latency p50 / p95 / max | 83 ms / 900 ms / 944 ms | 69 |

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

**Verification evidence.** `uv run mypy src` → success; `uv run pytest` → **479
passed**, measured. The suite has grown each round - 118, 146, 157, 178, 188,
216, 263, 291, 320, 354, 355, 374, 402, 438, 462, 467, 470, 475, now 479 - so a
number quoted from an earlier round no longer matches, and several places in
this document had gone on quoting one.
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

**Current fact.** At extractor version 16, DeepSeek Harness stores 550 discussions,
1510 comments, 123 doc files and **16,218 symptom signatures**. vLLM stores 489
discussions, 930 discussion comments, 1,001 issues, 1,001 pull requests, 8,824
Issue/PR comments and **65,580 symptom signatures**. vLLM Issues and PRs are deliberately
bounded and report `degraded`; one frozen historical incident is independently
stored as `reviewed`. DeepSeek discussion backfill is also still `degraded`.
A paced backfill (`rt sync … --backfill-pages N`) walks older history a few pages
at a time, persists its GraphQL cursor, resumes where it stopped, and never claims
`complete` while pages remain.

**Verification evidence.** Backfill measured across three runs: discussions
402 → 550, comments 1219 → 1510. Signature counts from those runs are **not**
comparable: they summed both mining passes. `discussions_backfill`
stayed `degraded` with `exhausted: false` throughout. `rt status` shows per-source
health and `data_as_of`.

**Remaining target.** Full backfill of all 5167 DeepSeek discussions and broader
vLLM Issue/PR coverage. The structurally different second repository is built;
see section 30 for the generalisation gaps it exposed.

**Blockers.** GraphQL point budget - a full walk cannot be done in one run, by
design.

---

## 7. Delivery

**Current fact.** Local gates all pass: `uv sync --extra dev --frozen`,
`ruff check .`, `ruff format --check .`, `mypy src` (76 source files),
`pytest` (**530 tests**), `evals/runner.py` (**70/71**, with the one declared
paraphrase recall gap retained), `docker compose up -d`, `db init`, `db ping`, `status`,
`uv build`, and a clean-venv install of the built wheel running `db init`, a
real `diagnose` against the synced database, and the same `diagnose` over stdio
MCP - not just `--help` and `--check`, which is what let a wheel that could not
run a single migration pass this list for several rounds
(`tests/test_wheel_install.py`, section 20).

**Verification evidence.** Commands and outputs are listed in the iteration report,
each checked by exit code rather than by reading the tail of the output.

**External CI has now run.** The repository was pushed to
`Liyuan1992/repo-troubleshooter` and the workflow executed on GitHub's runners:
run [33929933878](https://github.com/Liyuan1992/repo-troubleshooter/actions/runs/33929933878), commit `c8dabeb`, conclusion **success** in 1m06s.
Every step passed on a machine that is not this one - lint, format, strict
mypy, unit tests, a migration from an empty database, the database tests, the
packaging smoke test, and the installed wheel building a schema from an empty
database against the job's own PostgreSQL service.

That run contains the workspace-context collector, vLLM Issue/PR connector,
extractor-16 migration and both holdout implementations described below. The
live census results remain local evidence because that optional job step was
not enabled.

**What that run did not cover.** The `Live evaluation suite` step was **skipped**,
as designed: it is gated on `vars.RUN_LIVE_EVALS`, which is unset. So the live
tests, `evals/runner.py` and `evals/holdout.py` have still only ever run on this
machine. See section 28 for why enabling it is not a formality.

**Remaining target.** An independent hidden evaluation run by the evaluator - no
such result is claimed here - and a live CI block that could pass on the corpus
CI is able to sync.

**Blockers.** None. The remote exists and the default job is green.

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

Every change to how claims are read bumps that version and ships a migration
that deletes the mined rows and clears the recorded version, so an upgraded
database is *forced* through a rebuild rather than silently diagnosing against
stale features. The migration
`8f2c496d97c2_claim_reading_rules_invalidate_` invalidated version 11 when
structural code detection and relation statements changed what a clause
asserts, and `4b4507bd30a7_claim_strength_follows_target_source` invalidated
version 12 when claim strength moved onto the target's source, and
`02fc7f6885ae_quotation_regions_and_predicate_position` invalidated version 13
when quotation regions and predicate position changed what a clause asserts.
The current version is **14**.

Invalidation is one function, `store/signature_invalidation.py`, because doing
only half of it produced a false completion: the rows were deleted and the
version cleared, but `sync_state` still said `complete` with the row count of
the rows just deleted, so `rt status` reported a finished build over an empty
table. It now marks the source `stale` and zeroes the counts, and a finished
rebuild sets `complete` again - measured in both directions on the live
database.

**Verification evidence.** After `db init` on the existing database, `diagnose`
refused with `no symptom signatures are stored ... build them with:
repo-troubleshooter signatures <repo>` and exited non-zero; after
`rt signatures <repo> --rebuild` it answered again. The version 12 upgrade was
walked the same way on the live database: `alembic upgrade head`, then
`diagnose` exiting 1 with the stale-signature refusal, then a rebuild
(31,313 attempted / 15,689 inserted recorded), then the same query answering;
and again for versions 13 and 14. After the version 14 migration `rt status`
read `signatures stale / 0`, and after the rebuild `complete / 15689`.
`tests/test_signature_freshness.py` rewinds the stored version and asserts the
refusal on all three paths - engine (raises), CLI (non-zero exit,
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
| `rows_attempted` | (kind, value) pairs offered to the database | 31,299 |
| `rows_inserted` | of those, actually new | 15,682 |
| `rows_stored_total` | rows the repository holds afterwards | **15,682** |

Earlier rounds reported the attempted figure (32,882, and 19,185 before that) as
if it were storage. It was not.

These three come from `sync_state.stats` for source `signatures`, and a direct
`SELECT count(*)` on `symptom_signature` returns the same 15,682. They fell by
seven rows at extractor 15, where quotation stopped contributing subjects. 65
rows are marked `quoted`. An earlier
draft of this document quoted 31,314 / 15,689 while the database held
31,316 / 15,691 - numbers from a *different* rebuild than the one being
described. The figures here are re-read from the database after the extractor
version 12 rebuild rather than carried forward.

By kind, read from the database after the last gate of this round ran:
structural 5,142; behavior 4,666; component 2,073; subject_module 1,473;
subject_unresolved 716; error 575; subject_path 557; subject_package **181**;
subject_builtin 94; subject_dependency 71; cause 70;
subject_confirmed_non_primary 49; subject_conflicted 15.

The four subject counts moved because claim reading changed, and the figures
here had been left at the extractor-12 rebuild's. They are now taken last, after
every gate, for the reason section 20 gives: a document that quotes numbers a
test run produced is describing the test run.
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

---

## 16. The conservative rule, and what it costs

**Current fact.** An independent run of 20 unseen negatives returned 20 wrong
upgrades. Three families, one shape: anything the predicate or pronoun
vocabulary did not recognise read as *silence*.

| family | example | why it passed |
|---|---|---|
| unknown verbs | `It malfunctions`, `It times out` | `_read_predicate` missed, so the clause asserted nothing |
| unknown subjects | `Said package crashes`, `"It crashes"`, `- It crashes` | not on the pronoun whitelist, so filed as another entity's problem |
| unknown health words | `It is operational`, `It has no issues` | the exculpation never registered, so the package stayed actionable |

Extending the lists was declined. Two structural rules replace them:

* **an unreadable predicate is a claim, not silence.** A prose clause whose
  subject resolves to a package but whose predicate cannot be classified is
  recorded as an *uninterpreted claim*. While one is outstanding about a named
  package, no path, module or symbol may authorise an action about it;
* **an unresolvable subject dangles.** A noun phrase naming nothing we can see
  is neither attributed to the nearest package nor waved through as somebody
  else's problem.

Plus the conservative rule the review asked for: a package the report names but
whose condition is never established, and which the candidate never mentions,
cannot be reached by path, module or symbol alone.

Claims are only read from **prose**. Code or log output is not an assertion -
otherwise every pasted stack trace becomes a dangling claim. What counts as code
is decided structurally (section 17), not by the presence of one bracket.

**Verification evidence.** All 21 reported inputs (20 negatives plus the
disclosed `the theme engine crashes` boundary) return no unsafe action through
the installed CLI; the public set now carries all of them, run through the CLI
and a freshly launched `repo-troubleshooter-mcp` stdio process. The real
boot-graph symptom still upgrades to `dsh-v0.1.2-alpha.2`, as does the
`The server is healthy when importing @dsh...` variant that the `COPULA_RE` bug
had been mis-binding.

**The P1 was real**: `COPULA_RE` ended in a literal `` control character
instead of a word boundary - a shell-escaping mistake of mine that survived
review because the regex still compiled. Every bare adjective read as a copula.

**Cost, stated plainly.** The conservative rule loses recall, and two dev-set
incidents now abstain rather than match:

| case | why |
|---|---|
| `para-boot-graph-user-voice` | the long-standing paraphrase gap |
| `preset-mounting-discovery` | its log line was read as an unreadable claim, attributed to the only package named before it |

Both were registered as known gaps, excluded from Correct Action@1 rather than
deleted or passed by loosening the gate. `preset-mounting-discovery` has since
been recovered - not by loosening the claim gate but by fixing what counts as
code (section 17), which is what was misreading its log line. The paraphrase gap
remains, and recovering it needs a calibrated semantic channel - option 2 in the
review - not a longer vocabulary.

**Blockers.** None new. The unsafe-action figures continue to describe the
committed set only.

## 17. Two ways around the claim gate

**Current fact.** Review found that an unread claim did not always block the
package it was about. Two independent bypasses:

**A shared primary package licensed ignoring the claim.** The rule read
`if targeted and not has_package_identity:` - an unread claim only blocked when
nothing *else* had established identity. So `@dsh crashes! It is operational.`
sailed through: the first sentence agreed with the candidate, that agreement
counted as identity, and the second sentence - a contradiction in words the
system cannot read - was discarded. Agreement with a candidate is not permission
to stop reading. Claims whose subject **points at** a package (`It ...`,
`Said package ...`) now block that package unconditionally, whatever else the
report got right. Claims whose subject is ordinary prose merely following a
mention (`dsh web starts ...`) are weaker - they may not be about the package at
all - and still only count where nothing else establishes identity.

**A bracket counted as code.** The prose test was
`CODE_MARKER_RE = [{}()\[\]=<>|&]...`: any clause containing a parenthesis or a
square bracket was output, not a claim. `It is healthy (verified)` and
`It is fine [checked]` therefore lost their health statements entirely and the
package stayed actionable. Code is now recognised by structure - fenced blocks,
inline spans, stack frames, or punctuation density above 30% of the words with
at least two markers - and brackets alone prove nothing. The clause is parsed
*first*; only an unparseable clause is then tested for code.

**A relation statement is not a state claim.** `The project imports @dsh`
describes wiring, not condition. It was being filed as an unreadable claim, and
that cost three positives and the `preset-mounting-discovery` incident.

**Verification evidence.** Through the installed CLI: `@dsh crashes! It is
operational.`, `... fails! It has no issues.` and `... hangs! It passed every
health check.` now return no action, where all three previously upgraded; the
parenthesised and bracketed health statements abstain; the bare boot-graph
positive and three relation-prefixed variants of it all still upgrade to
`dsh-v0.1.2-alpha.2`. The public phrasing set in
`tests/test_failclosed_identity.py` carries all of them and runs each through
the installed CLI and a freshly launched `repo-troubleshooter-mcp` stdio
process. Evals returned to **68/69** with one gap.

**Remaining target.** The punctuation-density threshold is a heuristic and will
misjudge some short lines. A claim that is code *and* prose (a sentence ending in
an inline snippet) is still read as prose.

**Blockers.** None new. The unsafe-action figures continue to describe the
committed set only; the hidden acceptance set is not ours to run.

---

## 18. Claim strength, and where it comes from

**Current fact.** Two more ways past the claim gate, both found by review.

**Strength was read off the subject's wording.** An unread claim counted as
*pointed* - blocking unconditionally - only when its subject matched the pronoun
list. So a clause that spelled the package out was weaker than one that merely
pointed at it: `@dsh-client-modules crashes! @dsh-client-modules is operational.`
upgraded, as did `Said package is operational`, `The same package passed every
health check` and `This exact module has no issues`. Strength now follows
**where the target came from**, recorded on the assertion itself:

| source | what it means | strength |
|---|---|---|
| `explicit_package` | the clause names a package, or its subject is a package-shaped token | pointed |
| `resolved_anaphor` | the subject refers back to a package: a pronoun, or a noun phrase whose head noun names a package-kind | pointed |
| `proximity_guess` | ordinary prose that happens to follow a mention | weak |

No pronoun and no noun was added to any list. What changed is that the slot in
front of the noun is parsed - determiner, then modifiers, then head noun - so
`said package`, `the same package` and `this exact module` are recognised by
shape, while `the boot graph` still is not. Three structural conditions keep the
rule from over-reaching: the head noun may not be a hyphenated modifier
(`plugin-registered commands`), it must have a predicate behind it (`while
resolving a module` does not), and a named package only makes a claim explicit
when a predicate follows it (`@types/react bigint/ReactNode)` in a bug title is
an enumeration). Each of those was a real regression caught by the eval suite
before this was committed.

A pointed claim whose target cannot be resolved now blocks as well. `Said
package is operational` in a report naming no package we recognise is still a
claim about a package; which one is unknown, so no package is safe to act on,
and a shared path or symbol cannot settle it.

**A relation verb anywhere exempted the whole clause.** `_is_relation_statement`
searched the entire clause, so `It has no issues when using plugins` and `It
passed every health check after requiring dependencies` were filed as wiring and
their claims discarded. The verb now has to sit where a main predicate sits: in
the head segment, before any subordinator, within four words of the start.

**An inline span hid a claim while its neighbours were still read.** Backticks
made a clause code, so ``Diagnostic summary: `It is operational` `` lost its
claim - while the same region kept contributing paths and symbols towards an
upgrade. That is evidence counted in one direction only. An inline span is now
read as the quotation it is; fenced blocks and stack frames are still code.

**Verification evidence.** All seven reported inputs return no action through
the installed CLI, and so do the five from the previous round. The four
positives - the bare boot-graph report and three relation-prefixed variants -
still upgrade to `dsh-v0.1.2-alpha.2`. The seven are in the public phrasing set,
so each also runs through a freshly launched `repo-troubleshooter-mcp` stdio
process, which must abstain and must agree with the CLI. Unit tests pass;
evals are **68/69** with the one long-standing gap; ruff, format and mypy are
clean. Extractor version 12 -> 13 with an invalidation migration, walked on the
live database: upgrade, refusal, rebuild (31,313 / 15,689 / 15,689,
`extractor_version: 13`), answer.

**Remaining target.** Both windows this section described as a boundary were
unsafe paths, not future work: one more adjective (`this carefully audited
bundled runtime component`) put a subject outside the head-noun window, and a
relation verb four words in exempted a whole clause. Section 19 replaces both
with tests that do not have a window. Writing a boundary down and then writing
`Blockers: None` under it was wrong, and this section should have said so.

**Blockers.** None new. The unsafe-action figures continue to describe the
committed set only; the hidden acceptance set is not ours to run. *(No remote
was configured when this was written; section 28 records the CI run that closed
that gap.)*

---

## 19. Reading a claim, not matching its wording

**Current fact.** Review found five more ways past the gate. Every one of them
was the same mistake in a different place: a *list* or a *window* standing in
for a structural question.

**Strength was only recorded on claims we could not read.** A claim whose
predicate we *did* understand went down the other path, where the subject was
still matched against the pronoun list alone. `@x crashes! Said package is
healthy.` therefore produced an unresolved health claim, which the gate drops as
harmless. It is not harmless - it retracts the failure the sentence before it
reported. Both paths now ask the same question, and a health claim whose subject
points at a package blocks like any other pointed claim.

**A predicate we could see and not classify was dropped.** `It did not
malfunction` and `It wasn't defective` were read as UNKNOWN and then discarded,
recording nothing at all - exactly the silence the unread-claim path exists to
prevent. An unclassifiable predicate now goes down that path like any other.

**Predicate position was a word count.** A relation verb within four words of
the start exempted the clause, so `The package using our fallback shim remains
operational` was filed as wiring and deleted. Position cannot tell a reduced
relative clause from a wiring statement. What can: whether the clause predicates
anything *else*. If it does, the relation verb was not its main predicate.

**The subject head was a two-word window.** `This carefully audited bundled
runtime component remains operational` needed only one more adjective to fall
outside it. The head noun is now found by the predicate standing behind it, so
the number of modifiers in front is irrelevant. The same change made the pronoun
test survive a fronted adverbial: `At startup it is operational` had been
reading as prose about nothing.

**Code was a per-clause guess, not a region.** A fenced block introduced as
`Documentation example only:` had its package read as a second primary subject
of the report, which cancelled the conflict with the package the report actually
blamed. Fenced regions are now identified as regions - clause splitting cannot
see them, since it splits on newlines - and nothing inside one is a subject or a
claim. Mechanical strings are still taken from inside a fence: a pasted trace
evidences what the machine printed whoever pasted it, while *who is failing* is
a question quoted text cannot answer. Two smaller versions of the same error
went with it: `At startup ...` was matching the stack-frame pattern, which now
requires an actual frame behind `at`, and a quoted sentence is now its own
clause rather than a fragment of its introduction.

**And one over-refusal.** `Environment: @x version 0.1.2-alpha.1.` blocked
everything, because any alphabetic word after a mention counted as a predicate.
Predicate position is now decided by morphology - the closed auxiliary class,
or an inflected form - so `version` is not one and `remains`, `passed`, `did`
are.

**Verification evidence.** All ten reported inputs return no action through the
installed CLI; so do the seven from the round before and the five before that -
twenty-two negatives in one run. The five positives, including the environment
line that had been costing one, still upgrade to `dsh-v0.1.2-alpha.2`. The ten
are in the public phrasing set, so each also runs through a freshly launched
`repo-troubleshooter-mcp` stdio process. Unit tests pass; evals hold at
**68/69** with the one long-standing gap; ruff, format and mypy are clean.

**Remaining target.** This is shallow parsing and will still misread sentences a
parser would get right - a subject and its predicate separated by a relative
clause of their own, for instance. What has changed is that no rule now depends
on a distance or a list of words that a report can step outside of by writing
one more adjective.

**Blockers.** None new. The unsafe-action figures continue to describe the
committed set only; the hidden acceptance set is not ours to run. *(No remote
was configured when this was written; section 28 records the CI run that closed
that gap.)*

---

## 20. Quotation, finiteness, and a wheel that runs

**Current fact.** Four more defects, and one of them was not about reading text
at all.

### Quoted evidence finds candidates; it must not authorise acting on one

Deleting subjects and claims inside a fence while keeping its paths, symbols and
error strings was evidence counted in one direction, and it was reachable: a
block introduced as `Quoted from a retired vendor ticket:` still upgraded,
because the mechanical strings inside it established identity on their own. The
staging is now explicit, and matches the three-stage contract:

| stage | what quoted material may do |
|---|---|
| `retrieved_candidate` | its paths, symbols and errors may find candidates |
| `accepted_same_incident` | it may **not** carry identity alone - at least one shared identity feature has to come from outside the quotation |
| `actionable_incident` | unreachable without that bridge; the report stops at `retrieved_candidate` |

`quoted_only` records which identity values a report evidences *only* inside a
fence, computed by extracting twice - once as written, once with fenced regions
blanked out. `quoted_packages` and `quoted_claims` keep what the fence said
instead of dropping it silently, so a trace exists of material that was read and
set aside. Nothing depends on recognising the words `documentation` or
`example`: the structure of the report decides, not a label vocabulary.

A real report that pastes its own stack trace into a fence still upgrades,
because its prose names the same thing the trace does. A report that is *only*
a quotation now stops at `retrieved_candidate` - which is what it is.

### A relation verb has to be finite, and the only predicate

The previous round checked whether anything else in the clause was a *copula*.
`remains healthy` was caught only because `remains` happens to be one, and five
phrasings walked straight through: `The package using our fallback passed every
health check`, and the same shape with `survived`, `behaved`, `ran`, and with an
explicit relative pronoun. Three structural conditions replace that check:

* a bare `-ing` participle after a noun opens a reduced relative clause, and can
  only be a main predicate with an auxiliary in front of it (`we are using @x`);
* a finite verb behind `that`/`which`/`who` belongs to the relative clause;
* whatever the form, if any other word in the clause stands where a predicate
  stands, the relation verb was not the main one. `ran` is not inflected and no
  morphology test will catch it - the participle rule is what catches that one.

### The wheel installed cleanly and could not work

`uv build` produced a wheel that passed `--help` and `--check` and then failed on
the first command that touched the database: `Path doesn't exist:
...\Lib\migrations`, and over MCP the same missing resource was reported as
`database_unavailable`. Two causes, both mine: the project root was derived as
`parents[2]` of an installed source file, and the wheel shipped no migrations or
profiles at all. Both now live inside the package - `_migrations/` and
`repo_profiles/`, located through `importlib.resources` - and Alembic is
configured in memory rather than from an `alembic.ini` that an installed wheel
does not have.

`tests/test_wheel_install.py` builds the wheel, installs it into a virtual
environment of its own, and makes it do real work: `db init` running the
migrations, a `diagnose` that matches the incident, and the same `diagnose` over
stdio MCP. A `--help` gate could never have caught this, and said so for several
rounds while the wheel was unusable.

### The tests were writing to the database the document is measured from

`sync_state.stats` for `signatures` read `14 / 0 / 1` - a one-object build - not
because a build went wrong but because a *test* had run one. It committed, so no
fixture rollback could undo it, and every number this document quotes is read
out of that database. Two changes: the `session` fixture always rolls back, and a
session-scoped guard snapshots eleven upstream tables plus every `sync_state`
field a build writes, and fails the run if the suite changed any of them. The
bookkeeping test that caused it now works on a repository row of its own.

**Verification evidence.** Through the installed CLI: the fenced vendor ticket,
the same fence stating DSH is healthy, a fence labelled as documentation, and
all five reduced-relative phrasings return no action, each stopping at
`stopped_at: retrieved_candidate`. Seven positives still upgrade to
`dsh-v0.1.2-alpha.2`, including a real report that pastes its own trace into a
fence and `We are using @x`, which the finiteness rule has to keep reading as
wiring. `pytest` → **467 passed** with the read-only guard active; evals
**68/69**; ruff, format and mypy clean. The wheel gate ran against the live
database from a clean install. Signature counts and the by-kind table in
sections 6 and 9 were re-read from the database after the last gate.

**Remaining target.** *(Corrected in section 21.)* This paragraph described
`> ` replies and indented blocks as outside the quotation model and filed that
under "remaining target". It was not a recall gap: both produced **unsafe
upgrades**, demonstrated by holdout inputs. A known path to a wrong action is a
blocker, and writing `Blockers: None new` underneath one was wrong. Section 21
closes both.

**Blockers.** *(Superseded.)* At the time this section was written there was an
open P0: quotation was recognised only as a fence.

---

## 21. What a quotation is, and what "outside it" has to prove

**Current fact.** The previous round wrote a boundary into the "remaining
target" line and left `Blockers: None new` under it. That was wrong twice over:
the boundary was reachable by holdout inputs, and what it produced was not a
missed match but an **unsafe upgrade**. A known path to a wrong action is a
blocker. This section closes it.

### Quotation is not only three backticks

`> ` replies are quotation everywhere mail and issue trackers are used, and both
of these upgraded:

    Copied from a resolved ticket, not our incident:
    > <the whole symptom>

    Archived documentation example only:
        <the whole symptom>

`> ` runs are quotation unconditionally now. Indentation is *not*: four spaces
means "code" in Markdown, and reporters indent their own stack traces
constantly - treating indentation alone as quotation cost three real incidents
in the eval suite whose only evidence was an indented trace. An indented block
is quotation when a label in front of it hands the material off:
`Copied from`, `Archived … example`, `for reference`, `not our incident`. That
is a small closed vocabulary and the only one in the quotation model, because
nothing in the *shape* of an indented block distinguishes `Archived
documentation example only:` from `Here is its trace:`. Its limit is worth
stating plainly: a report that marks provenance in words not on that list reads
as the reporter's own.

Quotation is also found on the text **as written**. `normalize` collapses runs
of whitespace, so indentation had stopped existing before anything looked for
it.

### "One shared value outside the quotation" was not a bridge

The stage-two rule was: is there any shared identity value that is not
quoted-only. A generic `TypeError`, or merely naming the same file, satisfied
that and re-authorised an entire quoted ticket. The rule is now what the review
asked for: **run the identity gate again on the report with its quotations
removed, and require that view to identify the incident by itself, by the same
rules.**

That change also exposed a defect underneath it. `packages/loader/src/internal.ts`
yields a path, the symbol `internal.ts` and the component `loader` - one mention
of one file, counted three times, satisfying "a path plus a second class". A
symbol contained in a shared path is that path again, not independent evidence,
and no longer counts as the second class.

### The candidate side had to be symmetric

Provenance was computed for the query and nowhere else, so an upstream thread
that was itself largely a quotation could still identify a query on the strength
of what it had quoted. `symptom_signature` now carries a `quoted` column, set
during mining and read back into an unquoted view of the candidate, and the gate
runs on both views. 65 of 15,682 rows are marked quoted.

### English writes relative clauses with no marker

`The same package we use through the fallback ran cleanly afterward`, `The
package our fallback imports ran cleanly`, `The same package we rely on went
green` - a zero-marker relative clause, which no participle test or relative
pronoun can see, and whose main verbs (`ran`, `went`) are irregular, so no
morphology finds them either. What is visible is the subject: **a clause whose
subject is a package-kind noun phrase is predicating something of that package**,
so it is never a wiring statement about something else, and its claim is never
dropped. That reading is deliberately loose, and loose here fails closed - the
cost is a claim recorded, not a claim ignored.

### The tests were still writing to the synced database

Two things were true at once: the final snapshot matched, and the suite wrote to
the database while it ran. The stale-corpus tests rewound the *real* repository's
extractor version and committed, so for the length of the run the tool's corpus
was stale to any other process, and a killed test would have left it that way.
They now build a repository of their own. Separately, `diagnose` refreshed the
containment cache even under `persist=False`, and the CLI persists by default:
both were writing on every call a test made. `persist` now reaches the
containment cache, `diagnose --no-persist` exists, and every test that drives the
CLI uses it.

The guard compared row counts, which cannot see an update in place or a delete
paired with an insert. It compares a content digest per table now - and that is
how the containment writes were found at all.

### A recall claim that was not true

A test named `test_a_report_that_quotes_its_own_trace_still_upgrades` put the
whole symptom *outside* the fence and an unrelated snippet inside it. The claim
in section 20 rested on that test and was wrong. A report whose identifying
evidence is only inside a fence **abstains**, and that is now asserted under its
own name, together with the indented form of the same report, which still
upgrades.

**Verification evidence.** All eleven reported negatives return no action
through the installed CLI, each stopping at `stopped_at: retrieved_candidate`;
both reported positives behave as this section describes - one upgrades, the
other is the registered gap above. `pytest` → **467 passed** with the
content-digest guard active and no leftover rows; evals **68/69**; ruff, format
and mypy clean. Extractor 14 → 15 with a migration that adds the column and
invalidates the corpus; rebuilt to 31,299 / 15,682 / 15,682.

**Remaining target.** The provenance-label vocabulary is the one place a
report's wording still decides something, and it is small. Fences and `>` need
no words; indented blocks do.

**Blockers.** None known. That sentence means: no input demonstrated to produce
an unsafe action is currently unfixed - it does not mean none exists, and the
last four rounds each found some. The unsafe-action figures describe the
committed set; the hidden acceptance set is not ours to run. *(No remote was
configured when this was written; see section 28.)*

---

## 22. Where authority comes from

**Current fact.** *An architecture decision, taken by the project owner after
four rounds in which every fix was a new rule for reading English.*

The safety argument used to be **"we read every claim correctly"**. That
argument cannot be finished. The generator is a person writing English; the
reader is shallow parsing over regular expressions, and the last four rounds
each produced new sentence shapes that got past it - reduced relative clauses,
zero-marker relative clauses, fronted adverbials, quotation forms. Each finding
was real. The supply of them is not exhausted, and cannot be.

So the argument changed. **Free text finds candidates. It does not authorise
changing what someone runs.**

| stage | what free text may do | what it may not do |
|---|---|---|
| `retrieved_candidate` | find candidates by paths, symbols, errors, behaviour | - |
| `accepted_same_incident` | identify, and **refuse** - a contradiction in prose is still a reason to stop | - |
| `actionable_incident` | - | authorise a change |

Stage three now needs an **authorisation source**. Today there is one:
`--package NAME` (`packages` over MCP), naming a package the incident names as
**what failed**, directly or through the repository's own manifests.

*(Corrected in section 25. As first written this accepted a package the incident
named in any role at all - including one it had explicitly declared healthy -
which read "I run this package" as "I confirm it is what broke".)* The response carries
an `authorization` block saying whether anything authorised the answer, and when
nothing did, what *would* have been recommended:

    "authorization": {
      "authorized": false,
      "proposed_action": "upgrade",
      "proposed_target": "dsh-v0.1.2-alpha.2",
      "missing": ["the package you are running, as `--package NAME` ..."]
    }

Note what is deliberately asymmetric: **prose may veto, never authorise.** A
misreading that refuses costs recall, which is measurable and bounded. A
misreading that authorises costs a wrong instruction. That asymmetry is what
turns the unbounded parsing problem from a safety problem into a recall problem.

**What this costs.** Seven eval cases changed from `upgrade` to
`collect_more_info` when the gate landed - every one of them a positive, and no
negative changed in either direction. They were restored by stating the package
in the case, the same way `core_version`, `runtime` and `os` were already
stated: a user of this tool knows what they are running, and the whole premise
is "given my version and environment". Two new cases hold the unauthorised path
open on purpose, so the property is measured and not merely implemented:
`auth-prose-only-is-a-proposal` and `auth-unrelated-package-is-a-proposal`.

Fourteen unit tests changed the same way. They now state the package, which
makes the negatives *harder*: every fail-closed phrasing is now checked with the
user having named the very package the incident is about, and must still refuse.

**Verification evidence.** With `--package`, the flagship symptom answers
`upgrade → dsh-v0.1.2-alpha.2` as before. Without it, the same symptom returns
the same matched incident and the same evidence, `stopped_at:
accepted_same_incident`, action `collect_more_info`, and the proposal recorded.
Naming an unrelated package is not authorisation either. CLI and a freshly
launched stdio MCP process agree on all three. 470 tests pass; evals **70/71**
with the one long-standing gap; ruff, format and mypy clean.

**Remaining target.** *(Built; see section 23.)* The second authorisation
source - the user confirming the reading echoed back to them - was missing when
this section was written, and until it existed a report that named no package
could only be a proposal.

**Blockers.** None known, with the same meaning as in section 21: no input
demonstrated to produce an unsafe action is currently unfixed. The hidden
acceptance set is not ours to run. *(No remote was configured when this was
written; see section 28.)*

---

## 23. Showing the reading

**Current fact.** The second authorisation source is built: **the user
confirming what the tool understood**.

Shallow parsing will misread some reports, and no amount of further rules fixes
that. What makes a misreading survivable is showing it. Every answer now carries
an `understood` block holding everything the gate acted on, separated by where
it came from:

| field | where it came from |
|---|---|
| `packages_stated` | the user's own fields |
| `failing` / `used` / `cleared` / `contradictory` / `role_undetermined` | read out of prose |
| `quoted_packages` | found inside quoted material, shown rather than asserted |
| `unread_claims` | sentences that state a condition in words the reader could not classify |
| `core_version` / `runtime` / `os` | the user's own fields |
| `proposed_action` / `proposed_target` | what this reading would justify |
| `digest` | identifies this reading *and* this proposal |

Confirming the digest authorises the proposal: `--confirm <digest>` on the CLI,
`confirm` over MCP. On a terminal the CLI does it for the user - it prints the
reading, asks *Is that your situation?*, and answers again with the agreement.
The engine is deterministic, so the second run reaches the same reading.

**The digest is the point, not a formality.** It covers the reading and the
proposal together, so agreeing with one reading cannot authorise a different
one. A report that says something else produces a different digest, and an
earlier agreement no longer applies - "yes" is an answer about one situation,
never a setting that stays on.

**What this restores.** Everything the previous section cost. A user who pastes
an error and names nothing now sees what was understood, agrees, and gets the
same recommendation as before - with one interaction, and with the reading in
front of them when they agree to it.

**Verification evidence.** The flagship symptom with no package stated returns
the incident, the evidence, `requires_confirmation: true` and a digest;
confirming that digest returns `upgrade -> dsh-v0.1.2-alpha.2` with
`source: confirmed`. A forged digest authorises nothing. A digest taken from one
report and replayed onto a different one authorises nothing - measured with two
reports that differ by one sentence. All of it holds over a freshly launched
stdio MCP process as well as the installed CLI. 475 tests pass; evals **70/71**;
ruff, format and mypy clean.

**Remaining target.** The confirmation is only as good as the reading it shows.
`unread_claims` is capped at six entries for legibility, and a very long report
can therefore be agreed to without every unread sentence being visible - carried
in `docs/known_bypasses.md`.

**Blockers.** None known, with the meaning given in section 21.

---

## 24. What counts as done

**Current fact.** *The third of three decisions taken by the project owner. The
first two changed where authority comes from; this one changes what "passing"
means.*

Acceptance was being judged on adversarial input: a reviewer wrote a sentence
shape the reader had not seen, and each one that got through was a blocker. Every
such finding was real. The supply of them is also unbounded - the writer is a
person who knows English, the reader is shallow parsing - so judged that way
there is no state in which this tool is finished.

The criterion is now measured on the population the tool is for. `docs/threat_model.md`
says who that is, what is in scope, and - explicitly - that adversarially
constructed input is not. That is defensible only because of sections 22 and 23:
a misreading now costs a wrong *proposal*, which the reader sees and rejects,
rather than a wrong instruction.

**The measurement.** `evals/holdout.py` samples real reports out of the synced
corpus and asks about each one **with that report removed from the evidence**,
inside a transaction it rolls back. With the report still present it matches
itself and nothing is learned; removed, the honest answer for most reports is
"no incident here I know of", and anything else is a duplicate or a false
identity.

*(Superseded by section 25: the query was built from the report's body while
the corpus was mined from its title and body, so these numbers are measured with
a handicapped query, and `false_identity_rate` was a misnomer.)*

| gate | measured | threshold |
|---|---|---|
| `false_proposal_rate` | **0.00** (0/60) | ≤ 0.05, enforced - the run exits non-zero above it |
| `false_identity_rate` | **0.25** (15/60) | tracked, no threshold yet |

**Read, not assumed.** The 15 matches are listed and judged one by one in
`evals/holdout_judgement.md`. No threshold is set on the match rate, because one
sample of one repository is not a basis for setting one.

**And the 0.00 is not reassuring by itself.** None of the 15 reached a version
action because the incidents they matched carry no released fix to point at - the
version gate stopped them, not the identity gate. Had those incidents had
releases, some of the eight wrong matches would have become proposals. That is
written into the judgement file rather than left for a reader to notice.

**What the measurement found.** Four of the eight wrong matches are not incident
reports at all - `有没有可自定义重试时间的插件`, `怎么在不动旧版本的情况下更新版本？`,
`[Ideas] edit 工具是否应该…`, `方舟的 CodingPlan 套餐期望能够接入` - they describe a
wish, not a failure. Nothing in the pipeline yet asks whether a report describes
a failure at all. That is the clearest improvement lead in this document, and it
is recorded in `docs/known_bypasses.md` rather than acted on here. *(This
paragraph said five until section 25; it was a miscount of my own table.)*

**Adversarial findings after this point** stay in the suite as regressions. What
changes is their standing: a new phrasing that gets past the reader is a defect
recorded in `docs/known_bypasses.md`, not a blocker. A blocker is a path to a
wrong action, which now needs the authorisation gate to fail as well.

**Verification evidence.** `uv run python evals/holdout.py --sample 60 --seed
20260904` → `false_identity_rate 0.25`, `false_proposal_rate 0.00`, exit 0; the
threshold is enforced in the script and the run is part of the live CI block.
The judgement of all 15 pairs is committed. Developer suite unchanged at 70/71.

**Remaining target.** *(Partly built; see section 30.)* The second repository now
proves the other evidence shape works, but one reviewed vLLM chain is not a
second population-level false-proposal census.

**Blockers.** None known, with the meaning given in section 21.

---

## 25. Closing the authorisation and measurement contracts

**Current fact.** Five defects, all in what the previous three sections claimed
rather than in the parsing they replaced. Two are the contract, two are the
measurement, one is CI.

### Naming a package the incident merely mentions authorised it

`_authorize` accepted a stated package that appeared in *any* of the candidate's
roles - failing subject, listed dependency, explicitly cleared, contradictory, or
role never settled. So `--package @cordisjs/plugin-loader`, which #5084 only ever
mentions with an unresolved role, produced `authorized=true` and an upgrade. A
package the report had declared **healthy** would have done the same.

That read *I run this package* as *I confirm it is what broke*. Only the
incident's failing subject authorises now; every other role gets the
confirmation route and a message saying which role it found.

The alternative was two fields, `installed_packages` and `failing_packages`. One
field with narrowed meaning was chosen instead: a user who is unsure which
package broke is exactly the user this tool is for, and asking them to classify
first puts the question back on them.

### The confirmation did not show what was being confirmed

The echo printed the parse - roles read, unread claims, environment, proposed
action - and nothing about **which incident**, why it was accepted, or what
evidence stood behind the proposal. Those appeared only after the user answered.
The digest had covered the incident all along, but a digest is not something a
person can check, so the agreement was not informed and the safety argument that
rests on it did not hold.

`Understanding` now carries `incident_title`, `incident_url`, `identity_rule`,
`shared_evidence` and `evidence`, and the terminal prints all of it above the
question.

### The holdout query was poorer than the corpus it was compared against

Signatures are mined from **title and body** (`features_for_object`); the
holdout asked with the **body** alone. A methodological asymmetry of my own
making, and it hid a pair: corrected, the count goes 15 → 16, with **#2463**
appearing. The eligibility filter still measures the body alone on purpose -
that decides which reports are sampled, and changing it too would have made the
two runs incomparable.

### `false_identity_rate` was the wrong name for what was counted

Four of the sixteen matches are genuine duplicates, where matching is correct.
Calling the raw count a false-identity rate counted them as errors. The machine
number is `other_report_match_rate`; the adjudication lives in
`evals/holdout_judgement.md` and cannot be computed - whether two reports are
the same incident is a reading:

| | |
|---|---|
| `other_report_match_rate` | 16/60 = **0.267** (machine) |
| `confirmed_duplicate_rate` | 4/60 = 0.067 |
| `borderline_rate` | 4/60 = 0.067 |
| `adjudicated_false_identity_rate` | 8/60 = **0.133** |
| upper bound, every borderline counted wrong | 12/60 = **0.200** |

And a proposal rate needs a denominator. `proposal_opportunity_count` counts the
matches where a released fix existed to point at: **3**, of which **0** produced
a proposal. *(Section 26 corrects the description: it is the denominator of the
conditional rate only, never of the overall one, and this 0/60 is a regression
fixture rather than an estimate - wider samples of the same corpus do propose.)* Below that, a positive control asks the same way about a report
known to be a released incident and **must** reach a proposal - if it does not,
the run fails, because a zero would then be describing the harness.

*(Section 24 also said five of eight wrong matches were not failure reports. Four
were. Corrected there and in the judgement file.)*

### CI could not have reached any of this

The wheel step created and used `rt:rt@127.0.0.1:5432`, a database that does not
exist in that job; the service is `rt_claude:rt_claude@127.0.0.1:55447`. It
would have failed before the live holdout ran. My error, introduced with the
step itself.

**Verification evidence.** `--package @cordisjs/plugin-loader` and
`--package @nebula/theme-engine` no longer authorise and say why;
`--package @deepseek-ai/dsh-client-modules` still does. The echoed reading
carries the incident, the identity rule, the shared features and
discussion/commit/release evidence, asserted by
`test_the_echo_says_what_is_being_agreed_to`. Holdout at seed 20260904:
`other_report_match_rate 0.2667`, `proposal_opportunity_count 3`,
`false_proposal_rate 0.0`, positive control reaches a proposal, exit 0. 479
tests pass; evals 70/71; ruff, format, mypy clean.

**Remaining target.** Unchanged: one repository, one sample, one seed; and
nothing yet asks whether a report describes a failure at all - which is where
half the adjudicated errors come from.

**Blockers.** None known, with the meaning given in section 21. *(The corrected
CI has since run; see section 28.)*

---

## 26. Two denominators, and what the machine may call things

**Current fact.** *No identity-parsing changes in this round. Only the
measurement vocabulary, and what may be inferred from it.*

### The machine may not call anything false

`false_proposal_rate` counted proposals pointing at **another report**, and some
of those are genuine duplicates where proposing is correct. The machine cannot
tell which; separating them is a reading. The machine number is
`other_report_proposal_rate`, and only the hand adjudication in
`evals/holdout_judgement.md` produces `adjudicated_false_proposal_rate`.

### A rate needs the denominator it was measured against

Two are now reported, because they answer different questions:

* **overall** - proposals over every report sampled. The product rate: what a
  user of this corpus meets.
* **given opportunity** - the same proposals over the matches where a version
  action was reachable at all. The quality rate: of the times the question was
  really put, how often the answer pointed elsewhere.

`proposal_opportunity_count` is the denominator of the second only. Section 25
described it as the denominator of the rate over the sample, which it is not.

### The fixture's zero did not survive a wider sample

| run | reports | opportunities | proposals | overall | given opportunity |
|---|---|---|---|---|---|
| fixture, seed 20260904 | 60 | 3 | 0 | 0.000 | 0.000 |
| wide, seed 20260904 | 300 | 17 | 2 | 0.0067 | 0.118 |
| seeds 11 / 22 / 33 | 100 each | 4 / 10 / 7 | 1 / 3 / 2 | 0.01 / 0.03 / 0.02 | 0.25 / 0.30 / 0.286 |
| pooled 11/22/33 | 300 draws, 246 distinct | 21 | 6 | 0.020 | 0.286 |

The 60-report fixture stays, as a repeatable regression. It is no longer quoted
as a system error rate: it had three opportunities, and the same code on wider
samples proposes. Every run now records seed, sample size, eligible population,
corpus size, `data_as_of` and the numbers of the reports sampled, because a run
against CI's 200-discussion corpus is not comparable with one against this 550.

### Adjudicated

All five distinct proposals are read in the judgement file: **1 same, 1
borderline, 3 wrong**. Pooled over the three seeds:

| | overall | given opportunity |
|---|---|---|
| `adjudicated_false_proposal_rate` | 4/300 = **0.013** | 4/21 = **0.19** |
| upper bound, borderline counted wrong | 5/300 = 0.017 | 5/21 = 0.238 |

*(Section 27 supersedes this table twice over: `#46` was adjudicated wrongly
here, and the baseline is now a census of all 499 eligible reports rather than
pooled seeds.)*

**Read the conditional one.** The overall rate is low mainly because a version
action is rarely reachable. When one is, roughly **one proposal in five points
at a different incident**. That is the first number this project has that says
what the identity model is actually worth, and it is not a good one.

It is also not an action rate. None of these could become a recommendation
without the user naming the failing package or confirming the echoed reading -
the hard gate, unchanged and still asserted at zero.

### What stays frozen

`#1648 不支持打断模式吗` is a question about how to steer a running agent,
proposed against an unrelated bug. Reports that are not failure reports keep
producing this class of error. That is the next phase's target, and it is not to
be approached by adding verbs, package names or sentence-shape rules - those are
frozen until a product metric exists to move.

**Verification evidence.** Five runs, all exit 0 with the positive control
reaching a proposal: fixture 60, wide 300, and seeds 11/22/33 at 100 each.
479 tests pass; evals 70/71; ruff, format and mypy clean.

**Remaining target.** One repository. The conditional rate rests on 21
opportunities pooled, which is a small number to draw a conclusion from, and the
spread across seeds (0.118 to 0.30) says so.

**Blockers.** None known, in the sense given in section 21 - no input
demonstrated to produce a wrong *action* is unfixed.

**Delivery evidence still missing.** *(Closed in section 28: the repository was
pushed and the workflow ran.)* At the time this was written the repository had
no remote, so no CI run had executed.

---

## 27. A census, and a verdict I got wrong

**Current fact.** *Measurement only, again. No identity-parsing changes.*

### #46 was not the same incident

Section 26 counted `#46 无法启动 dsh` → `#1916 无法绑定非 127.0.0.1 的 IP 地址`
as a correct match, because both bodies contain `failed to apply loader entry`.
Reading past that line:

* #46 — `(@deepseek-ai/cordis-plugin-hmr): --expose-internals is required for
  HMR service`
* #1916 — `(@deepseek-ai/dsh-host-webserver): invalid config: $.host expected
  "127.0.0.1" | "0.0.0.0" but got "100.90.80.70"`

Two different failures sharing a generic outer wrapper. **Judging them the same
on that wrapper is the exact error this project spends its time refusing to make
in code**, made by hand in the judgement of that code. Corrected to wrong, and
every adjudicated number moved with it.

### The baseline is now a census

All 499 eligible reports, not a sample - so there is no sampling error left
inside this repository, and more seeds would add nothing. Section 30 now adds
the second-repository population and shows that this result does not generalise.

| | census (499) |
|---|---|
| matched another report | 102 |
| `proposal_opportunity_count` | 32 |
| machine proposals | 5 |
| `other_report_proposal_rate_overall` | 0.0100 |
| `other_report_proposal_rate_given_opportunity` | **0.1562** |
| adjudicated: 4 wrong, 1 borderline | |
| `adjudicated_false_proposal_rate` overall | **0.0080** (upper 0.0100) |
| `adjudicated_false_proposal_rate` given opportunity | **0.125** (upper 0.1562) |
| 95% interval (Wilson) on 4/32 | 0.050 - 0.281 |
| authorised version actions | **0** |

When a version action is reachable, about **one proposal in eight points at a
different incident**, and 32 trials cannot say much more precisely than
"somewhere between 5% and 28%". The vLLM census in section 30 is materially
worse conditionally and makes the product tradeoff explicit.

### The measurement code had no tests

`evals/holdout.py` decides what this project may claim about itself, and it was
rewritten twice with nothing covering it - while 479 tests covered everything it
measures. `tests/test_holdout_metrics.py` now covers the arithmetic, without a
database: which denominator each rate is over, that the conditional rate is
`null` rather than `0.0` when no opportunity existed, pooling across seeds and
distinct-versus-drawn report counts, the provenance fields, and the threshold's
exit decision - including that a failed positive control fails the run whatever
the rate says. The gate is a pure function now so a test can reach it.

**Verification evidence.** Extractor-16 census of 499, exit 0, positive control
reaching a proposal and authorised actions at zero. All five current proposals
are read in `evals/holdout_judgement.md`; #46 remains there as corrected
historical judgement. Current gate counts are reported in section 7.

**Remaining target.** One repository. And nothing yet asks whether a report
describes a failure at all - `#1648 不支持打断模式吗` is a question, proposed
against an unrelated bug, and that class keeps appearing.

**Blockers.** None known, in the sense given in section 21.

**Delivery evidence still missing.** *(Closed in section 28.)* At the time this
was written no CI run had executed.

---

## 28. External delivery evidence

**Current fact.** The workflow has run on a machine that is not this one.

Repository: [`Liyuan1992/repo-troubleshooter`](https://github.com/Liyuan1992/repo-troubleshooter),
public, at the owner's decision. Run [33863041642](https://github.com/Liyuan1992/repo-troubleshooter/actions/runs/33863041642) on commit `45c7511`,
job `check`, **success**, 1m16s. Every step green:

| step | result |
|---|---|
| Install dependencies from the lockfile (`uv sync --frozen`) | pass |
| Lint (`ruff check .`) | pass |
| Formatting (`ruff format --check .`) | pass |
| Types, strict (`mypy src`) | pass |
| Unit tests, no database, no network | pass |
| Fresh migration from zero (`db init`, `db ping`) | pass |
| Database tests (migration replay, idempotence) | pass |
| Packaging smoke test (build, clean venv, install, `--help`, `--check`) | pass |
| The installed wheel builds a schema from an empty database | pass |
| Live evaluation suite | **skipped** |

That last row matters as much as the nine above it.

### What CI has not run, and why it is not a formality

`Live evaluation suite` is gated on `vars.RUN_LIVE_EVALS`, which is unset. So
`pytest -m live`, `evals/runner.py` and `evals/holdout.py` have still **only ever
run on this machine**. Everything this document says about 70/71, about the
census, about the holdout rates, rests on local runs.

Turning the variable on would not simply extend the evidence, and it is worth
being plain about why rather than leaving a switch that looks flippable:

* the live block syncs **200** discussions; the corpus every number here is
  measured against holds **550**. Section 26 already says those are not
  comparable;
* the eval expectations name specific upstream artifacts - discussion #5084, a
  commit, a release. Whether a 200-discussion sync even contains them is not
  known, and if it does not, `evals/runner.py` fails for a reason that says
  nothing about the engine;
* `RT_GITHUB_TOKEN` is set from `secrets.GITHUB_TOKEN`, which is scoped to *this*
  repository. Whether it can read another repository's Discussions through the
  GraphQL API has not been tested.

So the live block is committed, has never executed anywhere, and would probably
fail on the corpus CI can build. That is a defect in the delivery pipeline, not
a gap in the engine, and it is recorded here rather than closed by flipping a
variable and hoping.

### Before pushing, run locally

Every non-live step of the workflow was executed on this machine first, in the
workflow's own order and with its own commands, against a database created for
that purpose so the measurement corpus was untouched: nine steps, nine exit
zeros. The CI run then reproduced all nine on Ubuntu with its own PostgreSQL
service. The local run is not evidence of CI; it is what made the CI run
predictable.

### One thing the push does that this document should say plainly

The repository is public, and its evaluation cases quote **real upstream
discussion text** - other people's bug reports, in their own words. That was
raised before pushing and the owner decided to publish. It is noted here so the
decision is visible rather than implicit in a URL.

**Verification evidence.** `gh run view 33863041642` reports conclusion
`success` for commit `45c7511`. The step list above is that run's, not a
description of the file.

**Remaining target.** A live CI block that can pass on the corpus CI is able to
sync; and an independent hidden evaluation, which remains the evaluator's to run
and is not claimed here.

**Blockers.** None.

---

## 29. Local workspace context

**Current fact.** The local CLI no longer requires a user to transcribe facts
that the current project already exposes. With `--repo`, `--version`,
`--runtime`, `--os` and `--package` omitted, it can read only bounded metadata
from the target workspace and detect:

* the evidence repository from a matching synced package or Git origin;
* the product version from an installed package manifest, the workspace's own
  manifest, or an exact dependency declaration, in that order;
* the runtime version from a fixed `--version` command selected by the repo
  profile, and the local operating system;
* related packages present in `package.json`, `pyproject.toml` or the relevant
  installed package manifest.

Every value is returned with its source. Explicit CLI fields always override
detected values. Detection only supplies environment data when the workspace is
actually connected to the selected evidence repository; running the CLI from an
unrelated checkout does not lend that checkout's Node or OS to a remote report.

Package presence remains non-authoritative. Detected packages are shown as
"found in the workspace, not assumed failing" and are never copied into the
structured `packages` field. A version proposal therefore still requires the
existing echoed confirmation, unless the user explicitly supplies `--package`.

The collector reads known metadata files only, caps their size, executes no
project scripts, retains no local paths, and does not read `.env`, raw logs,
tokens or configuration values.

**Verification evidence.** Pure tests cover automatic detection, installed
manifest precedence, manual overrides, unrelated workspaces, ambiguous
repositories, Git-origin fallback and the separation between detected packages
and authorization. An installed CLI subprocess test uses a temporary consumer
workspace and fake Node executable, detects repo/version/runtime/OS/package,
reaches the known proposal, and proves it still cannot act without confirmation.

**Remaining target.** Lockfiles whose package is not installed and whose
manifest declaration is a range do not yet produce an exact version. Reports
from containers or remote machines still need explicit overrides because local
host facts would be wrong.

**Blockers.** None.

---

## 30. Second repository: vLLM Issue/PR evidence chain

**Current fact.** `vllm-project/vllm` is now the structurally different second
repository. The live probe reports Issues as its primary support surface. Sync
stores Issues and pull requests independently, preserves comments, and records
GitHub-native `CLOSES` and `PR_MERGED_AS` relations. Historical seeds in the
profile are fetched directly, so a reproducible old incident does not require a
walk through tens of thousands of newer work items.

The reviewed baseline is issue #6461 (v0.5.2 returns 404 at `/metrics`), PR
#6463, merge commit `6366efc67b0aedd2c1721c14385370e50b297fb3`, and release
v0.5.3. Sync refuses to create the reviewed record unless GitHub says the PR
closes that issue, the observed merge commit equals the profile record, and Git
ancestry says v0.5.3 is the first stored release containing it. The record is
`reviewed`, `maintainer_confirmed=true`, `release_contains_change=true`, and
`runtime_verified=false` - containment is still not a runtime reproduction.

**Product acceptance.** An evaluator-authored report saying that Prometheus
metrics disappeared from `/metrics` on 0.5.2 matches #6461. Free text produces
an `upgrade → v0.5.3` proposal and a digest; only replaying the same request with
that digest makes it actionable. The same report on 0.5.3 is not told to upgrade
to 0.5.3, and an unrelated CUDA OOM report abstains. The triplet runs through
the installed CLI and a newly launched installed MCP stdio process, with Issue,
PR, commit and release evidence all cited.

**Generalisation gaps found and fixed.** This onboarding could not be completed
by YAML alone. The core had no Issue/PR connector, symptom retrieval excluded
Issues, single-segment API routes such as `/metrics` were not structural
identity, full mirrors were unnecessarily expensive for large repositories,
and partial clones tried to fetch every unreachable hex token found in logs.
Those were made repository-neutral capabilities: generic work-item ingestion,
HTTP-route features (extractor 16), blobless mirrors with full commit/tag graphs,
and local-only commit-reference resolution. No vLLM name or incident number was
added to the identity model; the frozen numbers live only in the evaluation
profile.

**Coverage and tradeoff.** Stored vLLM counts are 489 discussions, 930
discussion comments, 1,001 issues, 1,001 pull requests, 8,824 Issue/PR comments,
192 releases, 397 materialised commits and 65,580 signatures. Issue and PR coverage
is intentionally bounded and therefore `degraded`; docs were not snapshotted
because this acceptance tests the Issue→PR→Commit→Release path. That is a
product scope choice, not a claim of full repository support.

**Second-repository census.** Every one of the 1,001 stored Issues was evaluated
leave-one-out. Repository-template version fields supplied a current version for
230 reports; missing versions remained unknown rather than inheriting the frozen
0.5.2 control version. Ninety-six reports matched another Issue. Ten had both a
released-fix chain and a usable current version; all ten formed proposals, and
manual full-body adjudication found all ten to be different incidents. Machine
overall proposal rate: 10/1,001 = 0.0100. Adjudicated conditional false-proposal
rate: 10/10 = 1.000 (Wilson 95% interval 0.722-1.000). Authorised version actions:
**0/1,001**. See `evals/vllm_holdout_judgement.md` and the generated
`evals/reports/vllm-census-final.json`.

**Conclusion and remaining product decision.** Evidence-shape portability is
demonstrated, but the current lexical identity model's conditional precision is
not repository-independent. The hard package-or-confirmation gate contained all
ten wrong proposals, which supports the chosen product tradeoff rather than a
claim that proposal quality is acceptable. A future semantic or structured
identity channel needs a calibrated acceptance threshold against both censuses;
more package names, verbs and sentence-shape rules remain frozen.

**Blockers.** None for the second-repository acceptance. Broader historical
coverage remains bounded by GitHub API budget and time.

---

## 31. First-use flow and calibrated structured constraints

**Current fact.** `rt prepare PROFILE` now performs the repeatable first-use
sequence: it initialises or migrates the local database and invokes the same
bounded, resumable sync as `rt sync`. It does not start PostgreSQL, widen the
sync scope, or change the user's project. The command prints the concrete next
diagnosis command after the profile is ready.

**Report type.** Diagnosis now returns `report_assessment` before retrieval.
Callers can explicitly mark input as `failure`, `question`, or `idea`; the
default is `unknown`. Without a declaration, only non-quoted structured failure
evidence can permit retrieval. An unknown report stops for a concrete symptom
or a transparent `--report-kind failure` declaration. This declaration is not
an identity proof or authorisation source.

**Structured constraints.** Repeatable `--anchor KIND:VALUE` CLI fields and
MCP `anchors` accept exact `error`, `structural`, `subject_package`,
`subject_path`, and `subject_module` values. Every anchor must occur in the
candidate's non-quoted evidence; otherwise stage two records
`structured_anchor_mismatch` and cannot expose a matched incident. Passing an
anchor does not accept identity and cannot authorise advice. Existing explicit
failing-package and confirmation-digest sources remain the only authorities.

**Calibration evidence.** `evals/structured_anchor_calibration.py` was run
against the current two local corpora: all 14 manually adjudicated wrong
DeepSeek/vLLM report-to-candidate pairs were rejected by an anchor present in
the source report and absent from the candidate; reviewed positives #5084 and
vLLM #6461 still satisfied their anchors (16/16). This is calibration of the
one-way structured field contract, not a claim that free text can choose an
anchor or that a semantic identity model is ready to authorise anything.

**Verification evidence.** Pure contract tests cover report assessment and
quoted-anchor exclusion. Installed CLI tests assert that `--report-kind
question` never enters retrieval, a matching anchor preserves a proposal but
does not authorise it, and a mismatch reaches no incident. The complete suite
and fresh CLI/MCP verification are recorded only after the final gates below.

**Remaining target.** Run new leave-one-out censuses before replacing the
existing quality numbers: classification changes the measured population and
proposal opportunities. A semantic channel remains a separate, unbuilt product
decision requiring a held-out threshold across repositories.

---

## Not built

Dense retrieval / RRF, B1-B6 baselines, full backfill,
duplicate-of relations, claim entailment. From spec §22: GraphRAG, Neo4j,
multi-agent, long-term memory, web UI, automated GitHub replies, whole-codebase
embedding and automatic "which commit introduced the bug" remain deliberately
unbuilt.
