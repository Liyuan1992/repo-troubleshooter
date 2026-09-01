# Repository Troubleshooter

An evidence-constrained troubleshooting agent that understands how open-source software
evolves across versions.

> "I am on this version, in this runtime, and I hit this error. What should I actually do?"

It is not a GitHub chat, not a repository chat, not a generic RAG, not a coding agent.
It answers one narrow question well: **given your version and your environment, is this a
known incident, and what is the correct next action** — including the answer
`insufficient_evidence`, which is a first-class product state, not a failure.

## The claim this project has to earn

Generic semantic search finds a very similar problem and tells you to upgrade.
It does not know you are already past that release.

```text
Generic hybrid RAG:          Upgrade to 0.1.2-alpha.1
Repository Troubleshooter:   Your version already contains that change
                             (first release containing it: 0.1.2-alpha.1),
                             so this is probably a different incident.
                             Next: collect <X>.
```

If that case cannot be produced against real repositories with real evidence, the project
is not worth continuing. Everything below exists to make that case reproducible.

## Status

Phase **V0 — Data Spine**. See [docs/status.md](docs/status.md) for what is built, what is
verified, and what is deliberately not built yet.

## Why the data model looks paranoid

The whole product lives or dies on distinctions that most tools collapse:

| Not the same thing | | |
|---|---|---|
| relevant | ≠ | same problem |
| PR merged | ≠ | fix released |
| commit contained in a release | ≠ | runtime problem proven fixed |
| discussion closed / answered | ≠ | problem solved |
| first reported in version X | ≠ | introduced in version X |

So `git tag --contains` is stored as `ReleaseContainment` with the exact command
transcript, and it is labelled everywhere it is used:

> Commit containment proves the change is present in the tagged tree.
> It does not by itself prove the runtime symptom is resolved.

Raw facts and derived facts are also kept apart. Anything GitHub or git states directly is
an object row; anything we concluded is a `RelationAssertion` carrying `derivation`
(`github_native` / `git_deterministic` / `text_explicit` / `inferred`) and its evidence.
An inference never silently becomes a fact.

## Repositories do not share a shape

The first live target, `deepseek-ai/deepseek-harness`, has **Issues disabled and no public
PRs**. The textbook `Issue → PR → Commit` chain does not exist there, so nothing in the
core assumes it. Surfaces are probed at sync time and recorded on the repository row:

```bash
rt probe deepseek-ai/deepseek-harness
```

Repository-specific knowledge lives in `repo_profiles/*.yaml`, not in core code. Onboarding
a second repository should mean writing a profile. If it forces changes to retrieval,
evidence or version logic, the design is not yet general — and the README will not claim
"works with any GitHub repository" until that is demonstrated.

## Quick start

```bash
docker compose up -d
uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
cp .env.example .env   # RT_GITHUB_TOKEN is optional if `gh auth login` is done
rt db init
```

```bash
rt sync deepseek-harness --max-discussions 200
```

```bash
rt status deepseek-ai/deepseek-harness
```

```bash
rt contains deepseek-ai/deepseek-harness <commit-sha> --version 0.1.1-rc.2
```

## Layout

```text
src/repo_troubleshooter/
  connectors/github   GitHub GraphQL/REST: surface probe, discussions, releases
  connectors/git      clone/fetch mirror, tags, ancestry, docs snapshots
  store               PostgreSQL schema (FTS + pg_trgm + pgvector, one database)
  sync                idempotent, resumable, per-source health
  normalize           body -> content units (prose / code / log / config)
  relations           explicit cross-references only
  versions            version normalisation and release containment
  retrieval/evidence/diagnosis/verifier   later phases
  cli, mcp            the only two interfaces in V1
```

## Non-goals for V1

No GraphRAG, no Neo4j, no multi-agent, no long-term memory, no web UI, no automatic GitHub
replies, no whole-codebase embedding, no automatic "which commit introduced the bug".
PostgreSQL only, until a benchmark proves it is the bottleneck.
