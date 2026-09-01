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
* **subjects must not disagree** - when both sides name concrete subjects and none
  overlap, a shared error class is not identity (`different_subject`);
* **environment can only veto** - runtime/OS never contributes to identity.

**Verification evidence.** Committed suite: 25 negative controls, 0 false
incidents, 0 unsafe actions. The three reported leaks (CSP blocking `client.js`,
`cordis.yml` duplicate key, npm DNS failure) are rejected as
`different_root_cause` and asserted in `tests/test_identity_gate.py`. A surprise
holdout of 14 fresh wordings (not committed, run through the installed CLI)
initially exposed one false incident - an `ERESOLVE` peer-dependency query
matching a different `ERESOLVE` thread - which produced the `different_subject`
rule and its regression test; the holdout then passed 14/14 with 0 unsafe actions
and 0 false incidents.

**Remaining target.** Duplicate-vs-same-incident distinction. Measured over 109
threads, 3 queries matched a *different* thread; all three were reviewed by hand
and are plausible duplicate reports (sandbox escalation, pnpm install, subagent
model inheritance), so they are recorded as duplicates, not errors - but the
system does not yet say "duplicate of" explicitly.

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
Two further hand-written rewrites in the holdout also matched and produced the
same target. 18,555 signatures mined from 505 objects.

**Remaining target.** One documented recall gap, kept in the suite and excluded
from Correct Action@1 rather than deleted: `para-boot-graph-user-voice`, a rewrite
that shares no vocabulary at all ("startup list" for entries/batches, "fetches"
for preloads) and overlaps on exactly one behavioural feature. Closing it needs a
semantic channel; lowering the identity threshold instead would trade one recall
for false matches.

**Blockers.** None.

---

## 4. Measured results

**Current fact.** Committed suite: 55 cases - 10 incidents across 10 subsystems,
5 paraphrases, 25 negatives, 15 perturbations. 54 pass, 1 is the documented gap.

| Metric | Value | n |
|---|---|---|
| Correct Action@1 | 1.00 | 29 |
| negative false-incident rate | 0.00 | 25 |
| unsafe action rate (negatives) | 0.00 | 25 |
| unsafe action rate (environment contradictions) | 0.00 | 2 |
| version/release verdict accuracy | 1.00 | 15 |
| citation validity | 1.00 | 78 ids |
| claim-support validity (structural) | 1.00 | 55 |
| abstention recall | 1.00 | 25 |
| abstention precision | 0.58 | 43 |
| future-leakage violations | 0 | - |
| latency p50 / p95 / max | 74 ms / 700 ms / 724 ms | 55 |

**Verification evidence.** `python evals/runner.py` writes
`evals/reports/latest.json`; the hard gates are re-asserted in
`tests/test_eval_suite.py::TestHardGates`.

**Remaining target.** Abstention precision of 0.58 is honest, not good: it counts
every `collect_more_info` as an abstention, including the correct "your version
already contains this change" answers. A better denominator, and B1-B6 baselines,
are still to build. Claim-support validity is **structural only** - every claim
cites listed, resolvable evidence; nothing yet proves the excerpt entails the
sentence.

**Blockers.** B1-B6 need the alternative retrieval modes, which are not built.

---

## 5. Engineering contracts

**Current fact.** `mypy --strict` over `src` is clean (48 files, 0 errors) with no
blanket `ignore_errors` and no unexplained `type: ignore`; the only overrides are
`ignore_missing_imports` for third-party packages that ship no stubs (`mcp`,
`alembic`, `pgvector`). An MCP server (`repo-troubleshooter-mcp`) exposes exactly
two read-only tools over stdio, sharing the CLI's engine and contract. A database
that is down, empty, foreign or stale fails within ~3 seconds with a command to
run, never a traceback; MCP returns a structured error instead of hanging.

**Verification evidence.** `uv run mypy src` → success. `tests/test_mcp_roundtrip.py`
drives a real MCP SDK client: lists tools, calls both, and asserts CLI/MCP parity
on status, action, target, `incident.matched`, `stages.stopped_at` and the
evidence-id set. Dead-database run measured at 3 s, exit 1, no traceback.
`repo-troubleshooter-mcp --help` answers and exits instead of starting a session
(it previously hung - that was found by the delivery gate itself).

**Remaining target.** Structured errors for partial-sync states inside MCP tool
payloads beyond the current coverage note.

**Blockers.** None.

---

## 6. Data

**Current fact.** 550 discussions, 1510 comments, 123 doc files, 7 releases, 18,555
symptom signatures. Discussion coverage is still partial and reports `degraded`.
A paced backfill (`rt sync … --backfill-pages N`) walks older history a few pages
at a time, persists its GraphQL cursor, resumes where it stopped, and never claims
`complete` while pages remain.

**Verification evidence.** Backfill measured across three runs: discussions
402 → 550, comments 1219 → 1510, signatures 15,732 → 18,555; `discussions_backfill`
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

**Verification evidence.** Commands and outputs are listed in the iteration report.

**Remaining target.** A green CI run on a pushed commit.

**Blockers.** `blocker: no remote configured` - `git remote -v` is empty, and
pushing is the user's decision. The workflow (lint, format, strict mypy, unit
tests, fresh migration, database tests, packaging + MCP smoke) is committed but
has never executed anywhere.

---

## Not built

Dense retrieval / RRF, B1-B6 baselines, second repository, full backfill,
duplicate-of relations, claim entailment. From spec §22: GraphRAG, Neo4j,
multi-agent, long-term memory, web UI, automated GitHub replies, whole-codebase
embedding and automatic "which commit introduced the bug" remain deliberately
unbuilt.
