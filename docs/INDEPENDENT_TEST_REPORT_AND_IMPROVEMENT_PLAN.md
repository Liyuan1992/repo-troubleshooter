# Independent black-box test report and improvement plan

Date: 2026-09-01  
Evaluator boundary: this report was produced by invoking the installed product CLI directly. It does
not treat the project's own unit-test count or status document as acceptance evidence.

## Executive result

This artifact currently cannot participate in the product-level comparison because its formal CLI has
no `diagnose` command. The existing V0 data spine is useful foundation work, but it is not yet a
Repository Troubleshooter that can answer the target user question.

Current independent observations:

- `repo-troubleshooter diagnose --help` exits with code 2: `No such command 'diagnose'`.
- The installed console entry point and `db ping` work.
- `pytest` passes 41 tests.
- `ruff check .` fails with 21 errors, while `AGENTS.md` requires Ruff and pytest to be clean before
  work is called done.
- In the current three-project environment, `status deepseek-ai/deepseek-harness` fails because the
  database on port 55432 contains a different schema (`repository.host` is missing).
- The project accurately documents that retrieval, diagnosis, verification, benchmark and MCP work
  have not been built yet. Preserve that honesty while extending the product.

## Common evaluator cases

Implement a formal `diagnose` interface, then pass all five cases below through that interface. Do not
call internal helpers as the acceptance test.

Common environment unless a row overrides it:

```text
repo:    deepseek-ai/deepseek-harness
OS:      windows
runtime: node 24.11.1
error:   On Windows, dsh web starts but __DSH_BOOT__ has zero entries;
         client-modules reports HTML did not preload
         @deepseek-ai/dsh-client-modules/client.js;
         TypeError: e.indexOf is not a function
```

| Case | Changed input | Required result |
|---|---|---|
| old release | core version `0.1.2-alpha.1` | `upgrade` to `dsh-v0.1.2-alpha.2` or later compatible release; do not claim runtime verification |
| contained release | core version `0.1.2-alpha.3` | do not recommend the same upgrade; return `collect_more_info`/`insufficient_evidence` and explain containment |
| unresolved version | core version `nightly-2026-09-01` | mark `unresolved_version`; never infer that the year-like token is a newer semantic version |
| runtime contradiction | runtime `node 22.19.0`, core `0.1.2-alpha.1` | reject the Node 24 incident as directly applicable and abstain or report a conflict |
| negative control | error `PostgreSQL startup failed: connection refused at 127.0.0.1:5432 while applying migrations` | no incident match, no upgrade/config action, empty unsupported claims, explicit abstention |

Upstream evidence for the positive case:

- Discussion: https://github.com/deepseek-ai/deepseek-harness/discussions/5084
- Change: https://github.com/deepseek-ai/deepseek-harness/commit/675efe73f2d83202eccf145f8d9da14905c526d3
- Release: https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.1.2-alpha.2

The release note and commit containment support a release/action claim. They do not independently prove
that a user's runtime symptom was fixed.

## Required work

### P0 — restore a reproducible V0 foundation

1. Make `ruff check .` clean without weakening the configured rule set merely to hide generated
   migration errors.
2. Isolate the PostgreSQL runtime so sibling experiments cannot write a different Alembic schema into
   the same database:
   - bind only to `127.0.0.1`;
   - keep Claude on a documented unique database/container/volume identity;
   - if port 55432 is retained, ensure no sibling project also uses it;
   - add a startup schema-version check with a concise remediation message.
3. Prove a fresh-machine path, not only an already-populated database path:

   ```powershell
   docker compose up -d
   uv sync --extra dev
   uv run repo-troubleshooter db init
   uv run repo-troubleshooter db ping
   uv run repo-troubleshooter status deepseek-ai/deepseek-harness
   ```

4. Add a lockfile and a CI workflow that runs Ruff, unit tests, a fresh Alembic migration and a scoped
   PostgreSQL integration test.
5. Initialize a real Git history and commit only source, migrations, small fixtures and documentation.
   Do not commit `.env`, `.venv`, database data, mirrors, caches or live sync exports.

### P1 — implement the smallest trustworthy diagnosis slice

Build one narrow deterministic vertical slice before adding dense retrieval or an LLM:

1. Add a privacy-bounded diagnosis request contract containing repository, exact error/symptom, core
   version, runtime version, OS and optional plugin versions.
2. Add conservative error fingerprinting. Preserve exact error tokens and symbols that discriminate the
   incident; do not normalize unrelated connection failures into the same fingerprint.
3. Introduce a reviewed `IncidentResolutionRecord` or equivalent derived record with explicit evidence
   IDs, affected constraints, candidate change, first containing release, action, provenance and
   conflicts.
4. Implement exact and lexical retrieval with a real rejection threshold. Returning the best candidate
   is not sufficient if every score is weak.
5. Apply the version/runtime/OS applicability gate before synthesis. Unknown is not false and an
   unparseable version must not become `already_contains`.
6. Build an Evidence Packet and claim-specific output. Every claim must cite packet evidence; retrieved
   text alone must not become a fact.
7. Add `repo-troubleshooter diagnose` as the formal black-box interface. The deterministic provider must
   be sufficient to pass the five evaluator cases with no model key.
8. Add `get-evidence` only after evidence IDs can be resolved to source type, locator, source time,
   knowledge-available time and excerpt.

### P2 — expand only after the vertical slice passes

- Add PostgreSQL FTS/trigram/dense and RRF with score calibration and negative controls.
- Add typed relation expansion only where each edge has provenance and confidence.
- Add MCP only as a thin facade over the already-passing CLI/core contract.
- Build B1–B6 evaluation only after there are multiple independent incidents and negatives.
- Do not claim repository generality until a structurally different second repository works by profile
  alone.

## Independent test requirements

The new acceptance suite must not consist only of the single positive incident.

- At least 5 incident records from more than one repository or subsystem.
- At least 10 negative queries, including databases, authentication, networking and unrelated plugins.
- Version perturbations: older, first-containing, newer, missing and unparseable.
- Runtime/OS contradictions.
- Distractor documents sharing generic words such as `startup`, `config`, `plugin`, `error` and
  `Windows`.
- One fresh PostgreSQL migration/replay test proving source/revision/chunk idempotence.
- One CLI subprocess test that asserts exit code and parses the public JSON contract.

Do not special-case the exact evaluator strings, repository name, discussion number or evidence IDs.
The same logic must work with paraphrases and renamed fixture IDs.

## Completion gate

Do not mark this phase complete until all of the following are simultaneously true:

- all five common evaluator cases pass through the installed CLI;
- positive answers cite the discussion, change and release separately;
- negative and contradictory cases abstain without unsupported claims;
- `ruff check .` and `pytest` are clean;
- fresh database migration, status and idempotent replay pass;
- the database is isolated from the sibling artifacts;
- package build and installed-wheel CLI smoke pass;
- changes are committed, CI has run on that commit, and the CI run is green;
- `docs/status.md` is updated with current fact, verification evidence, remaining target and blockers as
  separate fields.

