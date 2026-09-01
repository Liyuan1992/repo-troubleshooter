"""Deterministic diagnosis engine.

No model, no key, no network. The chain is:

    fingerprint -> retrieve (with rejection) -> symptom evidence
                -> change resolution (git) -> containment (git ancestry)
                -> applicability gate -> claims -> verification -> action

Every branch that cannot complete that chain produces an abstention, not a
weaker guess. In particular:

* no candidate above threshold                 -> insufficient_evidence, abstain
* environment contradicts the incident         -> conflicting, collect_more_info
* the user's version cannot be ordered         -> unresolved_version, collect_more_info
* the change is already in the user's version  -> collect_more_info, and say so
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.diagnosis.contract import (
    Claim,
    DiagnosisRequest,
    DiagnosisResponse,
    IncidentSummary,
    RecommendedAction,
)
from repo_troubleshooter.evidence import packet as ev
from repo_troubleshooter.evidence.packet import EvidencePacket
from repo_troubleshooter.fingerprint.error import ErrorFingerprint, fingerprint
from repo_troubleshooter.relations.change_resolution import ChangeCandidate, resolve_change
from repo_troubleshooter.retrieval import candidates as retrieval
from repo_troubleshooter.store.models import (
    ContentUnit,
    GitCommit,
    IncidentResolutionRecord,
    Release,
    Repository,
    SourceObject,
    SyncState,
)
from repo_troubleshooter.sync.upsert import get_repository, upsert_commit
from repo_troubleshooter.verifier.claims import VerificationReport, verify
from repo_troubleshooter.versions import applicability as app_gate
from repo_troubleshooter.versions import semver
from repo_troubleshooter.versions.containment import (
    CONTAINMENT_MEANING,
    ContainmentResult,
    compute_containment,
    version_already_contains,
)


@dataclass
class DiagnosisDebug:
    """Everything needed to reproduce one query (spec section 41)."""

    fingerprint: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    change: dict[str, Any] | None = None
    containment: dict[str, Any] | None = None
    applicability: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "retrieval": self.retrieval,
            "change": self.change,
            "containment": self.containment,
            "applicability": self.applicability,
            "verification": self.verification,
            "timings_ms": self.timings_ms,
        }


def _sync_snapshot(session: Session, repo: Repository) -> tuple[dt.datetime | None, str, list[str]]:
    states = list(session.scalars(select(SyncState).where(SyncState.repo_id == repo.id)))
    if not states:
        return None, "stale", ["nothing has been synced for this repository"]

    successes = [s.last_success_at for s in states if s.last_success_at]
    data_as_of = max(successes) if successes else None
    statuses = {s.status for s in states}
    health = (
        "complete"
        if statuses <= {"complete"}
        else ("failed" if statuses == {"failed"} else "degraded")
    )

    notes: list[str] = []
    for state in states:
        if state.status != "complete":
            notes.append(f"{state.source}: {state.status}")
        stats = state.stats or {}
        if stats.get("capped"):
            notes.append(
                f"{state.source}: first sync was capped, so coverage of this source is partial"
            )
    return data_as_of, health, notes


def _object_text(session: Session, object_id: int, *, include_children: bool = True) -> str:
    ids = [object_id]
    if include_children:
        ids += list(
            session.scalars(select(SourceObject.id).where(SourceObject.parent_id == object_id))
        )
    rows = session.scalars(
        select(ContentUnit.text)
        .where(ContentUnit.object_id.in_(ids))
        .order_by(ContentUnit.object_id, ContentUnit.seq)
    )
    return "\n".join(rows)


def _sorted_releases(session: Session, repo: Repository) -> list[Release]:
    releases = list(
        session.scalars(
            select(Release).where(Release.repo_id == repo.id, Release.is_draft.is_(False))
        )
    )
    releases.sort(key=lambda r: semver.sort_key(r.version_norm or r.tag_name))
    return releases


def _release_by_tag(releases: list[Release], tag: str | None) -> Release | None:
    return next((r for r in releases if r.tag_name == tag), None) if tag else None


def _persist_incident_record(
    session: Session,
    repo: Repository,
    *,
    fp: ErrorFingerprint,
    symptom_object_id: int | None,
    symptom_ids: list[str],
    change_ids: list[str],
    release_ids: list[str],
    change: ChangeCandidate | None,
    containment: ContainmentResult | None,
    constraints: dict[str, Any],
    conflicts: list[str],
    evidence_level: str,
) -> str:
    """Cache the derived record so it can be reviewed and audited later.

    ``review_state`` starts at ``derived``: it is our conclusion, not a
    maintainer's. ``runtime_verified`` is never set here.
    """
    record = session.scalar(
        select(IncidentResolutionRecord).where(
            IncidentResolutionRecord.repo_id == repo.id,
            IncidentResolutionRecord.incident_key == fp.signature_hash,
        )
    )
    if record is None:
        record = IncidentResolutionRecord(repo_id=repo.id, incident_key=fp.signature_hash)
        session.add(record)

    record.symptom_signature = fp.signature[:2000]
    record.symptom_object_id = symptom_object_id
    record.symptom_evidence_ids = symptom_ids
    record.change_evidence_ids = change_ids
    record.release_evidence_ids = release_ids
    record.candidate_fix_commit = change.commit_sha if change else None
    record.first_release_containing_change = (
        containment.first_release_containing if containment else None
    )
    record.release_set = containment.containing if containment else []
    record.affected_constraints = constraints
    record.release_contains_change = bool(containment and containment.containing)
    record.runtime_verified = False
    record.evidence_level = evidence_level
    record.derivation = "inferred" if change else "text_explicit"
    record.conflicts = conflicts
    record.provenance = {
        "change": change.to_json() if change else None,
        "containment_meaning": CONTAINMENT_MEANING,
    }
    session.flush()
    return f"incident:{repo.id}:{fp.signature_hash[:12]}"


def diagnose(
    request: DiagnosisRequest,
    session: Session,
    *,
    git: GitRepo | None = None,
    persist: bool = True,
) -> tuple[DiagnosisResponse, EvidencePacket, DiagnosisDebug]:
    debug = DiagnosisDebug()
    packet = EvidencePacket()

    repo = get_repository(session, request.repo)
    if repo is None:
        return (
            DiagnosisResponse(
                status="insufficient_evidence",
                environment=request.environment_json(),
                recommended_action=RecommendedAction(
                    type="collect_more_info",
                    rationale=f"repository {request.repo} has not been synced into this instance",
                ),
                missing_information=[f"run `rt sync {request.repo}` first"],
                sync_health="stale",
            ),
            packet,
            debug,
        )

    data_as_of, sync_health, coverage_notes = _sync_snapshot(session, repo)
    environment = request.environment_json()
    runtime_name, runtime_version = request.runtime_name_version()

    fp = fingerprint(request.error, extra_context=request.question)
    debug.fingerprint = fp.to_json()

    base = DiagnosisResponse(
        status="insufficient_evidence",
        environment=environment,
        recommended_action=RecommendedAction(type="abstain"),
        data_as_of=data_as_of,
        sync_health=sync_health,
        coverage_notes=coverage_notes,
        fingerprint=fp.to_json(),
    )

    if fp.is_empty:
        base.recommended_action = RecommendedAction(
            type="collect_more_info", rationale="no error text or question was supplied"
        )
        base.missing_information = ["the exact error message or symptom"]
        return base, packet, debug

    # --- retrieval ---------------------------------------------------------
    result = retrieval.search(session, repo_id=repo.id, fingerprint=fp, limit=5)
    debug.retrieval = result.to_json()

    if not result.hits:
        best = result.rejected[0] if result.rejected else None
        base.recommended_action = RecommendedAction(
            type="abstain",
            rationale=(
                "no stored incident matches this error above the evidence threshold"
                + (f"; closest candidate rejected because {best.rejection_reason}" if best else "")
            ),
        )
        base.missing_information = [
            "whether this symptom has been reported upstream for this repository",
        ]
        if sync_health != "complete":
            base.missing_information.append(
                "coverage is partial, so absence of a match is not proof of absence"
            )
        return base, packet, debug

    top = result.hits[0]
    symptom_obj = session.get(SourceObject, top.object_id)
    symptom_item = ev.from_source_object(session, symptom_obj, role="symptom")
    symptom_id = packet.add(symptom_item)

    incident = IncidentSummary(
        matched=True,
        title=symptom_obj.title,
        url=symptom_obj.url,
        symptom_signature=fp.signature[:400],
        matched_tokens=top.matched_tokens[:10],
        score=round(top.score, 2),
        resolution_signal=symptom_obj.state,
    )

    symptom_text = _object_text(session, symptom_obj.id)
    symptom_fp = fingerprint(symptom_text)
    symptom_tokens = set(symptom_fp.discriminative) | set(fp.discriminative)

    # --- change and containment -------------------------------------------
    releases = _sorted_releases(session, repo)
    git = git or (GitRepo(repo.clone_path) if repo.clone_path else None)

    change: ChangeCandidate | None = None
    containment: ContainmentResult | None = None
    release_obj: Release | None = None

    if git is not None and releases:
        change = resolve_change(git, releases, symptom_tokens, symptom_text=symptom_text)
        debug.change = change.to_json() if change else None
        if change is not None:
            containment = compute_containment(session, repo, change.commit_sha, git=git)
            debug.containment = containment.to_json()
            release_obj = _release_by_tag(releases, containment.first_release_containing)

    change_ids: list[str] = []
    release_ids: list[str] = []
    if change is not None:
        commit_row = session.scalar(
            select(GitCommit).where(
                GitCommit.repo_id == repo.id, GitCommit.sha == change.commit_sha
            )
        )
        if commit_row is None and git is not None:
            # We just referenced this commit, so materialise it: an evidence id
            # that `get-evidence` cannot resolve is not evidence.
            info = git.commit_info(change.commit_sha)
            if info is not None:
                commit_row = upsert_commit(
                    session,
                    repo_id=repo.id,
                    sha=info.sha,
                    short_sha=info.short_sha,
                    subject=info.subject,
                    body=info.body,
                    author_name=info.author_name,
                    authored_at=info.authored_at,
                    committed_at=info.committed_at,
                    parents=info.parents,
                )
        if commit_row is not None:
            change_ids.append(packet.add(ev.from_commit(commit_row, files=change.files)))
        else:
            change_ids.append(
                packet.add(
                    ev.EvidenceItem(
                        id=ev.commit_evidence_id(change.commit_sha),
                        source_type="commit",
                        locator=change.commit_sha,
                        role="change",
                        title=change.subject,
                        excerpt=change.subject,
                        extra={"files": change.files, "derivation": "inferred"},
                    )
                )
            )
    if release_obj is not None:
        release_ids.append(packet.add(ev.from_release(release_obj)))

    # --- applicability -----------------------------------------------------
    constraints = app_gate.ExtractedConstraints()
    if release_obj is not None:
        # A release note is a maintainer statement: strong enough to contradict.
        explicit = app_gate.extract_constraints(
            release_obj.body,
            source="explicit",
            evidence_id=ev.release_evidence_id(release_obj.tag_name),
        )
        constraints.runtimes.extend(explicit.runtimes)
        constraints.operating_systems |= explicit.operating_systems

    observed = app_gate.extract_constraints(symptom_text, source="observed", evidence_id=symptom_id)
    constraints.runtimes.extend(observed.runtimes)
    constraints.operating_systems |= observed.operating_systems

    verdict = app_gate.evaluate(
        core_version=request.core_version,
        runtime_name=runtime_name,
        runtime_version=runtime_version,
        os_name=request.os,
        constraints=constraints,
    )
    debug.applicability = verdict.to_json()

    # --- claims ------------------------------------------------------------
    claims: list[Claim] = [
        Claim(
            type="symptom_match",
            value=f"matches {symptom_obj.kind} #{symptom_obj.number}: {symptom_obj.title}",
            confidence="high" if top.score >= 25 else "medium",
            basis="observed",
            evidence_ids=[symptom_id],
        )
    ]

    if change is not None and change_ids:
        claims.append(
            Claim(
                type="change",
                value=(
                    f"commit {change.short_sha} ({change.subject}) touches "
                    f"{', '.join(change.files[:3])} and is the most likely related change"
                ),
                confidence="medium",
                basis="inferred",
                evidence_ids=change_ids,
            )
        )

    if containment is not None and containment.first_release_containing and release_ids:
        claims.append(
            Claim(
                type="released_in",
                value=(
                    f"that change is first contained in {containment.first_release_containing}. "
                    + CONTAINMENT_MEANING
                ),
                confidence="high",
                basis="deterministic",
                evidence_ids=change_ids + release_ids,
            )
        )

    for conflict in dict.fromkeys(verdict.conflicts):
        claims.append(
            Claim(
                type="conflict",
                value=conflict,
                confidence="high",
                basis="explicit",
                evidence_ids=verdict.evidence_ids or [symptom_id],
            )
        )

    # --- action ------------------------------------------------------------
    action = RecommendedAction(type="collect_more_info", confidence="low")
    status: str = "insufficient_evidence"
    missing: list[str] = []
    conflicts: list[str] = list(verdict.conflicts)

    if verdict.status == app_gate.Applicability.HARD_CONTRADICTION:
        status = "conflicting"
        action = RecommendedAction(
            type="collect_more_info",
            rationale=(
                "the matched incident is bounded to an environment that contradicts yours, "
                "so it does not apply directly: " + "; ".join(verdict.conflicts)
            ),
            confidence="medium",
            evidence_ids=[symptom_id] + release_ids,
        )
        missing.append("an error report from your exact runtime and version combination")

    elif verdict.status == app_gate.Applicability.UNRESOLVED_VERSION:
        status = "insufficient_evidence"
        action = RecommendedAction(
            type="collect_more_info",
            rationale=(
                f"core version {request.core_version!r} cannot be ordered against release "
                "versions, so no upgrade or downgrade can be justified"
            ),
            confidence="low",
            evidence_ids=[symptom_id],
        )
        missing.append("a resolvable core version (a release tag or semantic version)")

    elif containment is None or not containment.first_release_containing:
        status = "probable" if top.score >= 25 else "insufficient_evidence"
        action = RecommendedAction(
            type="collect_more_info",
            rationale=(
                "a similar incident was found, but no released change could be tied to it, "
                "so no version action is justified"
            ),
            confidence="low",
            evidence_ids=[symptom_id],
        )
        missing.append("a linked change or release note for this symptom")

    else:
        already, explanation = version_already_contains(containment, request.core_version)
        if already is True:
            status = "insufficient_evidence"
            action = RecommendedAction(
                type="collect_more_info",
                rationale=(
                    f"your version already contains the related change "
                    f"(first contained in {containment.first_release_containing}); "
                    f"{explanation} Upgrading to that release would change nothing, so this is "
                    "probably a different incident."
                ),
                confidence="medium",
                evidence_ids=change_ids + release_ids,
            )
            missing.append(
                "a fresh reproduction on your current version (logs, and whether the symptom "
                "differs from the matched incident)"
            )
            claims.append(
                Claim(
                    type="affected_in",
                    value=(
                        f"user version {request.core_version} already contains the change; "
                        "the matched incident cannot be resolved by upgrading to "
                        f"{containment.first_release_containing}"
                    ),
                    confidence="high",
                    basis="deterministic",
                    evidence_ids=change_ids + release_ids,
                )
            )
        elif already is False:
            status = "probable"
            action = RecommendedAction(
                type="upgrade",
                target=containment.first_release_containing,
                rationale=(
                    f"your version {request.core_version} does not contain the related change; "
                    f"it is first contained in {containment.first_release_containing}. "
                    + CONTAINMENT_MEANING
                ),
                confidence="medium",
                evidence_ids=[symptom_id] + change_ids + release_ids,
            )
            claims.append(
                Claim(
                    type="action",
                    value=f"upgrade to {containment.first_release_containing} or later",
                    confidence="medium",
                    basis="deterministic",
                    evidence_ids=change_ids + release_ids,
                )
            )
        else:
            status = "insufficient_evidence"
            action = RecommendedAction(
                type="collect_more_info",
                rationale=f"version comparison is undecidable: {explanation}",
                confidence="low",
                evidence_ids=[symptom_id],
            )
            missing.append("a resolvable core version")

    if verdict.status == app_gate.Applicability.POSSIBLE_CONTRADICTION and status == "probable":
        status = "conflicting"
        action.confidence = "low"

    response = DiagnosisResponse(
        status=status,  # type: ignore[arg-type]
        environment=environment,
        incident=incident,
        applicability=verdict.to_json(),
        claims=claims,
        recommended_action=action,
        conflicts=conflicts,
        missing_information=missing,
        data_as_of=data_as_of,
        sync_health=sync_health,
        coverage_notes=coverage_notes,
        fingerprint=fp.to_json(),
    )

    # --- verification ------------------------------------------------------
    report: VerificationReport = verify(response, packet, session, repo, git)
    debug.verification = report.to_json()
    response.evidence = packet.refs()

    if persist and response.claims:
        evidence_level = (
            "medium" if change is not None and containment and containment.containing else "low"
        )
        incident.incident_id = _persist_incident_record(
            session,
            repo,
            fp=fp,
            symptom_object_id=symptom_obj.id,
            symptom_ids=[symptom_id],
            change_ids=change_ids,
            release_ids=release_ids,
            change=change,
            containment=containment,
            constraints=constraints.to_json(),
            conflicts=conflicts,
            evidence_level=evidence_level,
        )
        response.incident = incident

    return response, packet, debug
