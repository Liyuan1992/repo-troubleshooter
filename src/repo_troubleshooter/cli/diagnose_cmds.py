"""`diagnose`, `get-evidence` and `incidents` commands.

Kept in their own module so the black-box interface is easy to read: this file
is the whole public surface a caller (or an evaluator) exercises.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from repo_troubleshooter.diagnosis.contract import DiagnosisResponse
from repo_troubleshooter.store import db
from repo_troubleshooter.sync.upsert import get_repository


def render(response: DiagnosisResponse, console: Console, trace: Any = None) -> None:
    env = response.environment
    console.print()
    console.print("[bold]Detected[/bold]")
    detected = Table("field", "value", show_header=False)
    detected.add_row("Repository", str(env.get("repo")))
    detected.add_row("Core version", str(env.get("core_version") or "-"))
    detected.add_row("Runtime", str(env.get("runtime") or "-"))
    detected.add_row("OS", str(env.get("os") or "-"))
    console.print(detected)

    console.print()
    console.print(f"[bold]Status[/bold]  {response.status}")
    if response.incident.matched:
        console.print(
            f"Matched incident: {response.incident.title} "
            f"(score {response.incident.score}) {response.incident.url or ''}"
        )
        console.print(f"Applicability: {response.applicability.get('status')}")
    else:
        console.print("No incident matched above the evidence threshold.")

    if response.claims:
        console.print()
        console.print("[bold]Claims[/bold]")
        claims = Table("type", "confidence", "basis", "claim", "evidence")
        for claim in response.claims:
            claims.add_row(
                claim.type,
                claim.confidence,
                claim.basis,
                claim.value[:90],
                ", ".join(claim.evidence_ids),
            )
        console.print(claims)

    action = response.recommended_action
    console.print()
    console.print("[bold]Recommended action[/bold]")
    console.print(f"  {action.type}" + (f" -> {action.target}" if action.target else ""))
    if action.rationale:
        console.print(f"  [dim]{action.rationale}[/dim]")
    console.print(f"  confidence: {action.confidence}")

    if response.conflicts:
        console.print()
        console.print("[bold red]Conflicts[/bold red]")
        for conflict in response.conflicts:
            console.print(f"  - {conflict}")

    if response.missing_information:
        console.print()
        console.print("[bold]Missing information[/bold]")
        for item in response.missing_information:
            console.print(f"  - {item}")

    if response.evidence:
        console.print()
        console.print("[bold]Evidence[/bold]")
        evidence = Table("id", "role", "source", "locator", "url")
        for ref in response.evidence:
            evidence.add_row(ref.id, ref.role, ref.source_type, ref.locator, ref.url or "-")
        console.print(evidence)

    console.print()
    console.print(
        f"data_as_of: {response.data_as_of.isoformat() if response.data_as_of else 'never'}   "
        f"sync_health: [bold]{response.sync_health}[/bold]"
    )
    for note in response.coverage_notes:
        console.print(f"  [dim]coverage: {note}[/dim]")

    if trace is not None:
        console.print()
        console.print("[bold]Trace[/bold]")
        console.print_json(json.dumps(trace.to_json()))


def _render_understanding(console: Any, understood: Any) -> None:
    """Print the reading the answer is waiting on.

    Deliberately plain and complete: the point is that a wrong reading is easy
    to spot here, before it becomes a recommendation.
    """
    console.print("\n[bold]Before advising, check what I read:[/bold]")
    rows = (
        ("you stated these packages", understood.packages_stated),
        ("read as failing", understood.failing),
        ("read as used", understood.used),
        ("read as cleared", understood.cleared),
        ("read as contradictory", understood.contradictory),
        ("named, role unclear", understood.role_undetermined),
        ("quoted, not asserted", understood.quoted_packages),
        ("said in words I could not read", understood.unread_claims),
    )
    for label, values in rows:
        if values:
            console.print(f"  {label}: {', '.join(str(v) for v in values)}")
    for label, value in (
        ("version", understood.core_version),
        ("runtime", understood.runtime),
        ("os", understood.os),
    ):
        if value:
            console.print(f"  {label}: {value}")
    if understood.proposed_action:
        target = f" -> {understood.proposed_target}" if understood.proposed_target else ""
        console.print(f"  [bold]would recommend[/bold]: {understood.proposed_action}{target}")
    console.print(f"  (digest {understood.digest})")


def register(
    app: typer.Typer,
    console: Console,
    require_schema: Callable[[], None],
    fail: Callable[[str], NoReturn],
) -> None:
    @app.command("diagnose")
    def diagnose_cmd(
        repo: Annotated[str, typer.Option("--repo", help="owner/name")],
        error: Annotated[
            str | None, typer.Option("--error", help="Exact error text or symptom")
        ] = None,
        error_file: Annotated[
            Path | None, typer.Option("--error-file", help="Read the error text from a file")
        ] = None,
        question: Annotated[str | None, typer.Option("--question")] = None,
        core_version: Annotated[
            str | None, typer.Option("--version", "--core-version", help="Installed core version")
        ] = None,
        runtime: Annotated[
            str | None, typer.Option("--runtime", help='e.g. "node 24.11.1"')
        ] = None,
        os_name: Annotated[str | None, typer.Option("--os", help="windows | linux | macos")] = None,
        plugin: Annotated[
            list[str] | None, typer.Option("--plugin", help="name@version, repeatable")
        ] = None,
        config_key: Annotated[
            list[str] | None, typer.Option("--config-key", help="Config KEY NAME only, repeatable")
        ] = None,
        package: Annotated[
            list[str] | None,
            typer.Option(
                "--package",
                help=(
                    "A package you are running, by name. Repeatable. "
                    "Free text finds candidates; this is what authorises advice."
                ),
            ),
        ] = None,
        confirm: Annotated[
            str | None,
            typer.Option(
                "--confirm",
                help=(
                    "Agree with a reading this tool echoed back, by its digest. "
                    "The other way to authorise advice, for when the report speaks "
                    "for itself and you would rather not name packages."
                ),
            ),
        ] = None,
        as_json: Annotated[
            bool, typer.Option("--json", help="Machine-readable contract output")
        ] = False,
        debug: Annotated[
            bool, typer.Option("--debug", help="Include the reproduction trace")
        ] = False,
        no_persist: Annotated[
            bool,
            typer.Option(
                "--no-persist",
                help="Answer without recording anything: no incident record, no containment cache",
            ),
        ] = False,
    ) -> None:
        """Diagnose one problem against synced evidence. Deterministic; no model key needed."""
        require_schema()

        from repo_troubleshooter.diagnosis.contract import DiagnosisRequest, PluginSpec
        from repo_troubleshooter.diagnosis.engine import diagnose as run_diagnosis

        error_text = error
        if error_file:
            if not error_file.exists():
                fail(f"error file not found: {error_file}")
            error_text = error_file.read_text(encoding="utf-8", errors="replace")

        plugins = []
        for spec in plugin or []:
            name, _, version = spec.partition("@")
            plugins.append(PluginSpec(name=name, version=version or None))

        request = DiagnosisRequest(
            repo=repo,
            error=error_text,
            question=question,
            core_version=core_version,
            runtime=runtime,
            os=os_name,
            plugins=plugins,
            config_keys=list(config_key or []),
            packages=list(package or []),
            confirm=confirm,
        )

        from repo_troubleshooter.relations.signatures import SignaturesStale

        try:
            with db.session_scope() as session:
                response, _packet, trace = run_diagnosis(request, session, persist=not no_persist)
                # Somebody is there and the answer is waiting on them: show the
                # reading, ask, and answer again with their agreement. The
                # engine is deterministic, so the second run reaches the same
                # reading and the same digest.
                if (
                    not as_json
                    and confirm is None
                    and response.authorization.requires_confirmation
                    and response.understood is not None
                    and sys.stdin.isatty()
                ):
                    _render_understanding(console, response.understood)
                    if typer.confirm("Is that your situation?", default=False):
                        request = request.model_copy(update={"confirm": response.understood.digest})
                        response, _packet, trace = run_diagnosis(
                            request, session, persist=not no_persist
                        )
        except SignaturesStale as exc:
            fail("symptom signatures are stale or missing\n" + str(exc))

        if as_json:
            payload = response.to_json()
            if debug:
                payload["debug"] = trace.to_json()
            console.print_json(json.dumps(payload))
            return

        render(response, console, trace if debug else None)

    @app.command("get-evidence")
    def get_evidence_cmd(
        repo: Annotated[str, typer.Argument(help="owner/name")],
        evidence_id: Annotated[str, typer.Argument(help="e.g. ev:release:dsh-v0.1.2-alpha.2")],
        as_json: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Resolve an evidence id back to its source, time and excerpt."""
        require_schema()

        from repo_troubleshooter.evidence.packet import resolve as resolve_evidence

        with db.session_scope() as session:
            repository = get_repository(session, repo)
            if repository is None:
                fail(f"{repo} is not synced yet")
            item = resolve_evidence(session, repository, evidence_id)
            if item is None:
                fail(f"evidence id not found: {evidence_id}")
            payload = item.to_json()

        if as_json:
            console.print_json(json.dumps(payload))
            return

        table = Table("field", "value")
        for key in (
            "id",
            "source_type",
            "locator",
            "role",
            "url",
            "title",
            "source_event_time",
            "knowledge_available_time",
            "observed_at",
        ):
            table.add_row(key, str(payload.get(key) or "-"))
        console.print(table)
        if payload.get("excerpt"):
            console.print()
            console.print(f"[dim]{payload['excerpt']}[/dim]")

    @app.command("signatures")
    def signatures_cmd(
        repo: Annotated[str, typer.Argument(help="owner/name")],
        rebuild: Annotated[
            bool, typer.Option("--rebuild", help="Delete and re-mine every signature.")
        ] = False,
    ) -> None:
        """Mine symptom signatures from stored threads (sync does this too)."""
        require_schema()

        from repo_troubleshooter.relations.signatures import build_for_repository

        with db.session_scope() as session:
            repository = get_repository(session, repo)
            if repository is None:
                fail(f"{repo} is not synced yet")
            stats = build_for_repository(
                session,
                repository,
                rebuild=rebuild,
                progress=lambda message: console.print(f"[dim]{message}[/dim]"),
            )
        table = Table("metric", "value")
        for key, value in stats.to_json().items():
            table.add_row(key, str(value))
        console.print(table)

    @app.command("incidents")
    def incidents_cmd(
        repo: Annotated[str, typer.Argument(help="owner/name")],
        limit: Annotated[int, typer.Option("--limit")] = 20,
    ) -> None:
        """List derived incident resolution records (review state included)."""
        require_schema()

        from repo_troubleshooter.store.models import IncidentResolutionRecord

        with db.session_scope() as session:
            repository = get_repository(session, repo)
            if repository is None:
                fail(f"{repo} is not synced yet")
            records = session.scalars(
                select(IncidentResolutionRecord)
                .where(IncidentResolutionRecord.repo_id == repository.id)
                .order_by(IncidentResolutionRecord.updated_at.desc())
                .limit(limit)
            ).all()
            if not records:
                console.print("no incident records yet - run `rt diagnose` to derive them")
                return
            table = Table("key", "first release", "commit", "level", "review", "runtime verified")
            for record in records:
                table.add_row(
                    record.incident_key[:12],
                    record.first_release_containing_change or "-",
                    (record.candidate_fix_commit or "-")[:10],
                    record.evidence_level,
                    record.review_state,
                    "yes" if record.runtime_verified else "no",
                )
            console.print(table)
            console.print()
            console.print(
                "[dim]runtime_verified stays 'no' until a human confirms a reproduction; "
                "containment alone never sets it.[/dim]"
            )
