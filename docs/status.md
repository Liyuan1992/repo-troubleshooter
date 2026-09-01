# Status

Updated: 2026-09-01. Fields are deliberately separated: **current fact**, **verification
evidence**, **remaining target**, **blockers**. Nothing here counts as verified because a
unit test asserts it; each row names the observation that backs it.

Environment: Windows 11, Python 3.12, PostgreSQL 16 + pgvector in Docker, this project's
own container `rt-claude-postgres` on `127.0.0.1:55447`.

---

## 1. Repository shape (why the design looks like this)

**Current fact.** `deepseek-ai/deepseek-harness` has Issues disabled (0), zero public pull
requests, 5167 Discussions with an answerable `Q&A` category, 7 releases tagged
`dsh-v0.1.x`, and 372 tag-versioned files under `docs/`.

**Verification evidence.** `rt probe deepseek-ai/deepseek-harness` (live GraphQL), recorded
on the repository row as `surfaces` at every sync, including `has_pr_chain: false`.

**Remaining target.** A second, structurally different repository onboarded by profile
alone.

**Blockers.** None; not started. Until it is done, no generality claim is made.

The usable chain here is `Discussion → fix commit → tag ancestry → release`, plus
versioned docs. Nothing in the core assumes `Issue → PR → Commit`.

---

## 2. Diagnosis (`rt diagnose`) — the black-box interface

**Current fact.** A deterministic pipeline runs with no model, no key and no network:
fingerprint → exact/lexical retrieval with a rejection threshold → symptom evidence →
change resolution over git → containment by ancestry → applicability gate → claims →
verification → action. `rt get-evidence` resolves every cited id back to its source, time
and excerpt. `rt incidents` lists the derived `IncidentResolutionRecord`s.

**Verification evidence.** The five evaluator cases, through the installed CLI:

| Case | Input change | Result |
|---|---|---|
| old release | `--version 0.1.2-alpha.1` | `upgrade → dsh-v0.1.2-alpha.2`, citing discussion + commit + release separately |
| contained release | `--version 0.1.2-alpha.3` | `collect_more_info`, rationale says the version already contains the change |
| unresolved version | `--version nightly-2026-09-01` | `applicability: unresolved_version`, no upgrade |
| runtime contradiction | `--runtime "node 22.19.0"` | `status: conflicting`, `hard_contradiction`, conflict reported, no action |
| negative control | PostgreSQL connection error | `abstain`, no incident, no claims, no evidence |

Upstream ground truth for the positive case, checked by hand:
[discussion 5084](https://github.com/deepseek-ai/deepseek-harness/discussions/5084),
[commit 675efe73](https://github.com/deepseek-ai/deepseek-harness/commit/675efe73f2d83202eccf145f8d9da14905c526d3),
[release dsh-v0.1.2-alpha.2](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.1.2-alpha.2).
The first containing release is re-derived at query time by `git merge-base --is-ancestor`,
not read from the report.

**Remaining target.** Correct Action@1 measured over a larger frozen set, and the `vLLM`
evaluation track (spec Track A) which has not been started.

**Blockers.** None technical. The eval set is 29 cases; it is not yet large enough to
report a rate.

---

## 3. Change resolution — a defect found and fixed during this phase

**Current fact.** A change may be linked to a symptom only when the symptom text *names a
source path* and a `fix`/`perf`/`revert` commit *changed that path*. Merge commits,
commits touching more than 60 files, doc-only changes, and generic filenames
(`src/index.ts`, `package.json`) cannot create a link. Everything about the link is
labelled `inferred` and carries the git command that produced it.

**Verification evidence.** The first implementation scored commits by loose vocabulary
overlap. Run over 60 real threads it produced 20 confident `upgrade` answers, several
anchored on `chore: regenerate third-party notices`, `test(client): cover image echo
branches` and a release merge commit — fabricated causal links of exactly the kind this
product exists to prevent. After the rule change the same 60 threads produce 8 upgrade
answers, each anchored on a path the thread itself named and a same-subsystem `fix(...)`
commit. `tests/test_change_resolution.py` encodes each old false link so it cannot return.

**Remaining target.** Corroboration from a second independent signal (a maintainer answer
or a release-note line that names the same symptom), which would allow raising confidence
above `medium`.

**Blockers.** None; deliberately deferred until there is a metric that shows it helps.

---

## 4. Evidence, claims and verification

**Current fact.** Confidence attaches to a claim, never to the answer. Every claim carries
`basis` (`explicit` / `deterministic` / `observed` / `inferred`) and evidence ids. After
synthesis, a verifier re-checks that each cited release exists in the store and each cited
commit exists in the mirror, drops claims that fail, and downgrades an action whose support
was dropped. Requests are redacted at the door (tokens, keys, JWTs, emails, home paths);
config **key names** only.

**Verification evidence.** `tests/test_cli_contract.py` asserts, through the installed CLI,
that every claim cites listed evidence, that every evidence id resolves via `get-evidence`,
and that secrets pasted into `--error` do not appear anywhere in the JSON contract.

**Remaining target.** Claim-level entailment checking (does the excerpt actually support
the sentence), which today is structural only.

**Blockers.** Needs a model; the deterministic provider cannot do entailment.

---

## 5. Data spine and sync

**Current fact.** Bare-mirror clone/fetch, releases + tags, Q&A discussions with comment
truncation recorded, docs snapshots per tag, explicit-reference relations, and per-source
sync health. Re-running a sync is idempotent; an upstream edit appends a revision instead
of overwriting.

**Verification evidence.** `tests/test_migration_replay.py` creates a throwaway database,
migrates from zero, ingests the same source twice and asserts identical counts, then
asserts that an edited body adds exactly one revision and leaves exactly one current.
Two defects were found this way and fixed:

* re-sync duplicated every `RelationAssertion` whose `dst_object_id` was NULL (PostgreSQL
  treats NULLs as distinct in a unique constraint) — fixed with `NULLS NOT DISTINCT` plus a
  de-duplicating migration;
* a derived commit had no `git_commit` row, so its evidence id could not be resolved —
  the engine now materialises it and resolution falls back to the mirror.

**Remaining target.** A paced full backfill of all 5167 discussions.

**Blockers.** GraphQL point budget: a full first sync exceeds the hourly limit. Capped runs
report `degraded`, never `complete`, and every diagnosis carries that coverage note.

---

## 6. Isolation, build and CI

**Current fact.** This project owns its PostgreSQL identity end to end: compose project
`repo-troubleshooter-claude`, container `rt-claude-postgres`, volume `rt-claude-pgdata`,
database `rt_claude`, bound to `127.0.0.1:55447`. Every command that touches the database
first checks that the schema is this project's and at head, and prints remediation if not.
`ruff check .`, `ruff format --check .` and `pytest` are clean; `uv.lock` is committed; a
CI workflow runs lint, format, unit tests, a fresh migration, database tests and an
installed-wheel smoke test.

**Verification evidence.** A sibling artifact had previously migrated its own schema into
the shared database on port 55432 and replaced this project's tables; that is what the
guard now detects (`rt db ping` reports `owned_by_this_project`). Local runs: 89 tests
pass, `ruff check .` passes, `uv build` + install into a clean venv + `repo-troubleshooter
diagnose --help` succeed.

**Remaining target.** A green CI run on a pushed commit.

**Blockers.** No git remote is configured and pushing is the user's call, so CI has not yet
executed anywhere. The workflow is committed but unproven.

---

## 7. Evaluation suite

**Current fact.** 29 frozen cases in `evals/cases/`: 5 incidents across 5 subsystems
(loader/client-modules, web-search, agent-presets, CLI profile boot, session persistence),
15 negative controls (databases, authentication, networking, infrastructure, unrelated
toolchains, plus two pure-distractor queries built only from corpus-common words), and 9
version/runtime/OS perturbations.

**Verification evidence.** `python evals/runner.py` → 29/29, report written to
`evals/reports/latest.json`. Queries are paraphrases; the runner special-cases no
repository, discussion number or evidence id beyond what a case file states.

**Remaining target.** Baselines B1–B6 from the spec, and cases from a second repository.

**Blockers.** Baselines need the alternative retrieval modes (dense, RRF), which are not
built.

---

## Not built

Dense retrieval and RRF, typed relation expansion beyond literal references, MCP server,
`vLLM` evaluation track, B1–B6 baselines. From spec §22: GraphRAG, Neo4j, multi-agent,
long-term memory, web UI, automated GitHub replies, whole-codebase embedding, and automatic
"which commit introduced the bug" remain deliberately unbuilt.
