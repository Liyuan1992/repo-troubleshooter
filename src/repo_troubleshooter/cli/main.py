"""Command line interface.

V1 ships a CLI and an MCP server, not a web UI. Every command that reports data
also reports how fresh and how complete that data is - a partial world must
never be presented as a complete one.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from repo_troubleshooter.config import get_settings
from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.connectors.github.client import GitHubClient
from repo_troubleshooter.connectors.github.probe import probe_repository
from repo_troubleshooter.profiles.loader import list_profiles, load_profile
from repo_troubleshooter.store import db
from repo_troubleshooter.store.models import (
    ContentUnit,
    GitCommit,
    RelationAssertion,
    Release,
    Repository,
    SourceObject,
    SyncState,
)
from repo_troubleshooter.sync.orchestrator import sync_repository
from repo_troubleshooter.sync.upsert import get_repository
from repo_troubleshooter.versions.containment import (
    CONTAINMENT_MEANING,
    compute_containment,
    version_already_contains,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Evidence-constrained troubleshooting for versioned open-source software.",
)
db_app = typer.Typer(no_args_is_help=True, help="Database lifecycle.")
app.add_typer(db_app, name="db")

console = Console()


def _fail(message: str) -> None:
    console.print(f"[bold red]error[/bold red] {message}")
    raise typer.Exit(code=1)


def _require_schema() -> None:
    """Refuse to run against an empty, stale, or foreign database."""
    from repo_troubleshooter.store.migrate import SchemaMismatch, require_schema

    try:
        require_schema()
    except SchemaMismatch as exc:
        _fail("database check failed\n" + str(exc))


# --- db ---------------------------------------------------------------------


@db_app.command("init")
def db_init() -> None:
    """Create extensions and run migrations to head (idempotent)."""
    from repo_troubleshooter.store import migrate

    created = db.ensure_extensions()
    action = migrate.upgrade_to_head()
    console.print(f"extensions ready: {', '.join(created)}")
    console.print(f"schema: {action} (revision {migrate.current_revision()})")


@db_app.command("ping")
def db_ping() -> None:
    """Check the connection AND that the database belongs to this project."""
    from repo_troubleshooter.store.migrate import schema_health

    try:
        version = db.ping()
    except Exception as exc:  # noqa: BLE001
        _fail(f"cannot reach PostgreSQL at {get_settings().database_url}: {exc}")

    console.print(version)
    health = schema_health()
    console.print(f"database: {get_settings().database_url}")
    console.print(
        f"schema: revision={health.revision or '-'} head={health.head or '-'} "
        f"owned_by_this_project={health.owned}"
    )
    if not health.ok:
        _fail(health.remediation())


# --- profiles and probing ---------------------------------------------------


@app.command("profiles")
def profiles_cmd() -> None:
    """List configured repo profiles."""
    table = Table("profile", "role", "surfaces (declared)", "docs")
    for profile in list_profiles():
        surfaces = profile.support_surfaces
        table.add_row(
            profile.repo,
            profile.role,
            f"discussions={surfaces.discussions} issues={surfaces.issues} prs={surfaces.prs}",
            ",".join(profile.docs.paths) or "-",
        )
    console.print(table)


@app.command("probe")
def probe_cmd(
    repo: Annotated[str, typer.Argument(help="owner/name")],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Detect which support surfaces a repository actually has."""
    owner, _, name = repo.partition("/")
    if not name:
        _fail("repository must be given as owner/name")
    with GitHubClient() as client:
        surfaces = probe_repository(client, owner, name)
    if as_json:
        console.print_json(json.dumps(surfaces.to_json()))
        return
    table = Table("field", "value")
    table.add_row("repository", surfaces.full_name)
    table.add_row("default branch", surfaces.default_branch or "?")
    table.add_row("primary support surface", surfaces.primary_support_surface)
    table.add_row("issues", f"{surfaces.issue_count} (enabled={surfaces.issues_enabled})")
    table.add_row("pull requests", str(surfaces.pull_request_count))
    table.add_row("discussions", str(surfaces.discussion_count))
    table.add_row("answerable categories", ", ".join(surfaces.answerable_categories()) or "-")
    table.add_row("releases / tags", f"{surfaces.release_count} / {surfaces.tag_count}")
    table.add_row("issue->pr->commit chain available", "yes" if surfaces.has_pr_chain else "no")
    console.print(table)


# --- sync -------------------------------------------------------------------


@app.command("sync")
def sync_cmd(
    profile_name: Annotated[str, typer.Argument(help="Profile name or owner/name")],
    full: Annotated[bool, typer.Option("--full", help="Ignore the incremental watermark.")] = False,
    max_discussions: Annotated[
        int | None, typer.Option("--max-discussions", help="Scope guard; 0 = unlimited.")
    ] = None,
    no_docs: Annotated[
        bool, typer.Option("--no-docs", help="Skip versioned docs snapshots.")
    ] = False,
    no_git: Annotated[
        bool, typer.Option("--no-git", help="Skip clone/fetch (GitHub only).")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Sync a repository into the local spine. Safe to re-run and to interrupt."""
    _require_schema()
    try:
        profile = load_profile(profile_name)
    except FileNotFoundError as exc:
        _fail(str(exc))

    report = sync_repository(
        profile,
        full=full,
        max_discussions=max_discussions,
        include_docs=not no_docs,
        include_git=not no_git,
        progress=lambda msg: console.print(f"[dim]{msg}[/dim]"),
    )

    if as_json:
        console.print_json(json.dumps(report.to_json()))
        return

    table = Table("source", "status", "objects", "changed", "seconds", "detail")
    for name, source in report.sources.items():
        table.add_row(
            name,
            source.status,
            str(source.objects),
            str(source.changed),
            f"{source.duration_s:.1f}",
            source.error or json.dumps(source.detail)[:80],
        )
    console.print(table)
    console.print(f"sync health: [bold]{report.health}[/bold]")


@app.command("status")
def status_cmd(
    repo: Annotated[str | None, typer.Argument(help="owner/name; omit for all")] = None,
) -> None:
    """Show what is stored, how fresh it is, and how complete the sync was."""
    _require_schema()
    with db.session_scope() as session:
        repositories = (
            [get_repository(session, repo)] if repo else list(session.scalars(select(Repository)))
        )
        repositories = [r for r in repositories if r is not None]
        if not repositories:
            console.print("nothing synced yet - run `rt sync <profile>`")
            return

        for repository in repositories:
            console.print(f"\n[bold]{repository.full_name}[/bold]  ({repository.clone_path})")

            counts = Table("stored", "count")
            for label, stmt in (
                (
                    "discussions",
                    select(func.count())
                    .select_from(SourceObject)
                    .where(
                        SourceObject.repo_id == repository.id, SourceObject.kind == "discussion"
                    ),
                ),
                (
                    "discussion comments",
                    select(func.count())
                    .select_from(SourceObject)
                    .where(
                        SourceObject.repo_id == repository.id,
                        SourceObject.kind == "discussion_comment",
                    ),
                ),
                (
                    "doc snapshots (files)",
                    select(func.count())
                    .select_from(SourceObject)
                    .where(SourceObject.repo_id == repository.id, SourceObject.kind == "doc_file"),
                ),
                (
                    "releases",
                    select(func.count())
                    .select_from(Release)
                    .where(Release.repo_id == repository.id),
                ),
                (
                    "commits (referenced)",
                    select(func.count())
                    .select_from(GitCommit)
                    .where(GitCommit.repo_id == repository.id),
                ),
                (
                    "content units",
                    select(func.count())
                    .select_from(ContentUnit)
                    .where(ContentUnit.repo_id == repository.id),
                ),
                (
                    "relations",
                    select(func.count())
                    .select_from(RelationAssertion)
                    .where(RelationAssertion.repo_id == repository.id),
                ),
            ):
                counts.add_row(label, str(session.scalar(stmt)))
            console.print(counts)

            health = Table(
                "source", "status", "last success", "watermark", "objects seen (total)", "error"
            )
            states = session.scalars(
                select(SyncState).where(SyncState.repo_id == repository.id)
            ).all()
            for state in states:
                health.add_row(
                    state.source,
                    state.status,
                    state.last_success_at.isoformat(timespec="seconds")
                    if state.last_success_at
                    else "-",
                    state.watermark.isoformat(timespec="seconds") if state.watermark else "-",
                    str(state.objects_seen),
                    (state.last_error or "")[:60],
                )
            console.print(health)

            statuses = {s.status for s in states}
            overall = (
                "complete"
                if statuses and statuses <= {"complete"}
                else ("stale" if not statuses else "degraded")
            )
            newest = [s.last_success_at for s in states if s.last_success_at]
            console.print(
                f"data_as_of: {max(newest).isoformat(timespec='seconds') if newest else 'never'}   "
                f"sync_health: [bold]{overall}[/bold]"
            )


# --- containment ------------------------------------------------------------


@app.command("contains")
def contains_cmd(
    repo: Annotated[str, typer.Argument(help="owner/name")],
    commit: Annotated[str, typer.Argument(help="Commit sha (full or abbreviated)")],
    version: Annotated[
        str | None, typer.Option("--version", help="A user's installed version, e.g. 0.1.1-rc.2")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Which releases contain a commit - and what that does and does not prove."""
    _require_schema()
    with db.session_scope() as session:
        repository = get_repository(session, repo)
        if repository is None:
            _fail(f"{repo} is not synced yet - run `rt sync {repo}`")
        if not repository.clone_path:
            _fail(f"{repo} has no local mirror - run `rt sync {repo}` without --no-git")

        git = GitRepo(repository.clone_path)
        result = compute_containment(session, repository, commit, git=git)

        payload = result.to_json()
        if version:
            verdict, explanation = version_already_contains(result, version)
            payload["user_version"] = version
            payload["already_contains_change"] = verdict
            payload["explanation"] = explanation

        if as_json:
            console.print_json(json.dumps(payload))
            return

        if not result.commit_known:
            _fail(f"commit {commit} is not present in the local mirror of {repo}")

        table = Table("release", "contains change")
        for tag in result.containing:
            table.add_row(tag, "[green]yes[/green]")
        for tag in result.not_containing:
            table.add_row(tag, "no")
        for tag in result.unknown:
            table.add_row(tag, "[yellow]unknown[/yellow]")
        console.print(table)
        console.print(f"resolved sha: {result.resolved_sha}")
        console.print(
            f"first release containing the change: {result.first_release_containing or '-'}"
        )
        console.print(
            f"first stable release containing the change: "
            f"{result.first_stable_release_containing or '-'}"
        )
        if version:
            verdict, explanation = version_already_contains(result, version)
            label = {
                True: "[green]YES[/green]",
                False: "[red]NO[/red]",
                None: "[yellow]UNKNOWN[/yellow]",
            }[verdict]
            console.print(f"\nversion {version} already contains this change: {label}")
            console.print(f"[dim]{explanation}[/dim]")
        console.print(f"\n[yellow]{CONTAINMENT_MEANING}[/yellow]")


from repo_troubleshooter.cli import diagnose_cmds  # noqa: E402  (needs `app` above)

diagnose_cmds.register(app, console, _require_schema, _fail)


if __name__ == "__main__":  # pragma: no cover
    app()
