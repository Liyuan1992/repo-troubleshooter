# Repository Troubleshooter

English | [简体中文](README.zh-CN.md)

Turn an error report, version, and runtime environment into a troubleshooting proposal
backed by real repository evidence.

Repository Troubleshooter searches discussions, releases, commit history, and documentation
to answer:

- Is this a known incident?
- Has a fix been released?
- Does the user's current version already contain that change?
- What should the user try next, and what evidence supports it?

## Example

```text
Input
  report:   client boot graph is empty after startup
  version:  0.1.2-alpha.1
  runtime:  Node.js 24.11.1 on Windows
  package:  @deepseek-ai/dsh-client-modules

Output
  incident: known loader incident
  action:   upgrade
  target:   dsh-v0.1.2-alpha.2
  evidence: discussion, fixing commit, and containing release
```

When the evidence is not strong enough, the result is `insufficient_evidence` instead of an
unsupported recommendation.

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), and Docker Compose.

```bash
docker compose up -d
uv sync --extra dev
cp .env.example .env
uv run rt db init
```

On PowerShell, use `Copy-Item .env.example .env` instead of `cp`. A GitHub token is optional
when the GitHub CLI is already authenticated with `gh auth login`.

## Sync a repository

```bash
uv run rt profiles
uv run rt probe deepseek-ai/deepseek-harness
uv run rt sync deepseek-harness --max-discussions 200
uv run rt status deepseek-ai/deepseek-harness
```

Sync is safe to re-run and resume. The `status` command reports source freshness and
completeness.

## Diagnose a report

Save the report as `report.txt`, then run:

```bash
uv run rt diagnose --repo deepseek-ai/deepseek-harness --error-file report.txt --version 0.1.2-alpha.1 --runtime "node 24.11.1" --os windows --package @deepseek-ai/dsh-client-modules
```

`--package` identifies the component the user believes is failing. It may be repeated. If the
failing package is unknown, omit it and review the proposal and evidence before confirming.

Use `--json` for machine-readable output and `--debug` to inspect the candidate and decision
trace. Run `uv run rt diagnose --help` for the complete interface.

## MCP

The package includes a read-only stdio MCP server with `diagnose` and `get_evidence` tools.

```bash
uv run repo-troubleshooter-mcp --check
uv run repo-troubleshooter-mcp
```

## Current status

The first repository validated with real data is
[`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness). The CLI and
MCP interfaces are available now. The tool produces recommendations and evidence but does not
modify the user's project. Validation on a second repository is still in progress.

## Documentation

- [Current status](docs/status.md)
- [Threat model](docs/threat_model.md)
- [Known limitations](docs/known_bypasses.md)
