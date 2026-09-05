# Repository Troubleshooter

English | [简体中文](README.zh-CN.md)

Turn an error report into a troubleshooting proposal backed by real repository evidence.

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

Detected from the workspace
  version:  0.1.2-alpha.1
  runtime:  Node.js 24.11.1 on Windows
  packages: @deepseek-ai/dsh

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
uv run rt prepare deepseek-harness --max-discussions 200
```

On PowerShell, use `Copy-Item .env.example .env` instead of `cp`. A GitHub token is optional
when the GitHub CLI is already authenticated with `gh auth login`.

`prepare` creates or upgrades the local database and performs the first bounded
sync. It is safe to re-run; interrupted syncs resume normally. It does not
start Docker or widen the scope beyond the flags you provide.

## Manage repository evidence

```bash
uv run rt profiles
uv run rt probe deepseek-ai/deepseek-harness
uv run rt sync deepseek-harness --max-discussions 200
uv run rt status deepseek-ai/deepseek-harness
```

Sync is safe to re-run and resume. The `status` command reports source freshness and
completeness.

## Diagnose a report

Save the report as `report.txt`. From this checkout, point the command at the user's project:

```bash
uv run rt diagnose --workspace /path/to/project --error-file report.txt
```

When `rt` is installed on `PATH`, run it directly inside the project instead:

```bash
cd /path/to/project
rt diagnose --error-file report.txt
```

The CLI detects the evidence repository, installed product version, runtime, operating system,
and related packages from the local workspace. It shows every value and its source. Detected
dependencies are never silently treated as the failing component.

When a known incident has a released fix but the failing package was not supplied as a field, the
CLI displays its reading, matched incident, evidence, and proposed version before it asks for a
confirmation digest. Confirming means only “this displayed interpretation describes my
situation”; it authorises a recommendation, never an upgrade, configuration change, or any
change to the project.

`--repo`, `--version`, `--runtime`, `--os`, and `--package` remain available as overrides for
remote reports, containers, and ambiguous workspaces.

For a vague report, `--report-kind failure` is a transparent declaration that it is an observed
failure; use `--report-kind question` or `idea` to prevent incident retrieval. It is not an
authorisation source. If you know an exact error code, API path, source path, module, or package,
add `--anchor KIND:VALUE` (repeatable) to reject candidates that do not demonstrate that fact.
Anchors only narrow candidates; they do not authorise advice.

Use `--json` for machine-readable output and `--debug` to inspect the candidate and decision
trace. Run `uv run rt diagnose --help` for the complete interface.

## MCP

The package includes a read-only stdio MCP server with `diagnose` and `get_evidence` tools.

```bash
uv run repo-troubleshooter-mcp --check
uv run repo-troubleshooter-mcp
```

## Current status

Two structurally different repository evidence paths work with real data:
[`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness) through
Discussions, and [`vllm-project/vllm`](https://github.com/vllm-project/vllm) through an
Issue → pull request → commit → release chain. The CLI and MCP interfaces are available now.
A real-report census also shows that free-text proposals can point at the wrong incident, so
the echoed evidence and confirmation step are required. The tool does not modify the user's
project.

## Documentation

- [Current status](docs/status.md)
- [Threat model](docs/threat_model.md)
- [Known limitations](docs/known_bypasses.md)
