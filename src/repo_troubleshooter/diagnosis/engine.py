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
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.diagnosis.contract import (
    Authorization,
    Claim,
    DiagnosisRequest,
    DiagnosisResponse,
    IncidentSummary,
    RecommendedAction,
    ReportAssessment,
    StageReport,
    Understanding,
)
from repo_troubleshooter.diagnosis.intent import assess_report
from repo_troubleshooter.evidence import packet as ev
from repo_troubleshooter.evidence.packet import EvidencePacket
from repo_troubleshooter.fingerprint import features as feat
from repo_troubleshooter.fingerprint.error import ErrorFingerprint, fingerprint
from repo_troubleshooter.relations.change_resolution import (
    ChangeCandidate,
    resolve_change,
    resolve_linked_change,
)
from repo_troubleshooter.relations.signatures import load_features, require_fresh_signatures
from repo_troubleshooter.retrieval import pipeline
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
from repo_troubleshooter.versions.packages import PackageFamily


@dataclass
class DiagnosisDebug:
    """Everything needed to reproduce one query (spec section 41)."""

    fingerprint: dict[str, Any] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    change: dict[str, Any] | None = None
    containment: dict[str, Any] | None = None
    applicability: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "features": self.features,
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
    record.derivation = change.derivation if change else "text_explicit"
    record.conflicts = conflicts
    record.provenance = {
        "change": change.to_json() if change else None,
        "containment_meaning": CONTAINMENT_MEANING,
    }
    session.flush()
    return f"incident:{repo.id}:{fp.signature_hash[:12]}"


def _understanding(
    request: DiagnosisRequest,
    features: feat.SymptomFeatures,
    incident: IncidentSummary,
    action: RecommendedAction,
    report_assessment: ReportAssessment,
    identity: Any | None = None,
    evidence: list[str] | None = None,
) -> Understanding:
    """Everything the gate acted on, in a form a person can check."""
    understood = Understanding(
        packages_stated=sorted(request.packages),
        workspace_packages=sorted(request.detected_packages),
        failing=sorted(features.subject_packages),
        used=sorted(features.subject_dependencies),
        cleared=sorted(features.subject_confirmed_non_primary),
        contradictory=sorted(features.subject_conflicted),
        role_undetermined=sorted(features.subject_unresolved),
        quoted_packages=sorted(features.quoted_packages),
        identity_anchors=list(request.identity_anchors),
        report_assessment=report_assessment,
        unread_claims=sorted(
            {
                str(a.get("cue", ""))
                for a in (features.pointed_unread_assertions + features.unresolved_state_assertions)
                if a.get("cue")
            }
        )[:6],
        core_version=request.core_version,
        runtime=request.runtime,
        os=request.os,
        context_sources=request.context_sources,
        context_warnings=request.context_warnings,
        incident_title=incident.title,
        incident_url=incident.url,
        identity_rule=getattr(identity, "rule", None),
        shared_evidence={
            name: values
            for name, values in (getattr(identity, "shared", None) or {}).items()
            if values
        },
        evidence=list(evidence or []),
        proposed_action=action.type,
        proposed_target=action.target,
    )
    # The digest covers the reading *and* the proposal, so a confirmation
    # cannot be carried across to a different reading or a different action.
    material = json.dumps(
        {
            "repo": request.repo,
            "incident": incident.url or incident.title,
            **understood.model_dump(mode="json", exclude={"digest"}),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    understood.digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return understood


def _authorize(
    session: Session,
    request: DiagnosisRequest,
    candidate: Any,
    repo: Repository,
    understood: Understanding,
) -> Authorization:
    """Did the user state something that authorises acting on this candidate?

    Today there is one source: a package named in `request.packages` that the
    candidate also names, directly or through the repository's own manifests.
    A second source - the user confirming the reading echoed back to them -
    belongs to the next step and will be recorded here the same way.

    Note what is *not* a source: a package read out of the report's prose. That
    is the whole point of the split. Prose still finds candidates and still
    refuses them - a contradiction in prose is a reason to stop, and being
    wrong there costs recall, not safety - but it no longer says yes.
    """
    if request.confirm:
        if request.confirm == understood.digest:
            return Authorization(authorized=True, source="confirmed")
        return Authorization(
            authorized=False,
            requires_confirmation=True,
            missing=[
                f"the confirmation given ({request.confirm}) is for a different reading than "
                f"the one below ({understood.digest}); check what changed before agreeing"
            ],
        )

    if not request.packages:
        return Authorization(
            authorized=False,
            requires_confirmation=True,
            missing=[
                "either the package you are running, as `--package NAME`, or a confirmation "
                f"of the reading below: `--confirm {understood.digest}`"
            ],
        )

    stated = {name.strip().lower() for name in request.packages if name.strip()}
    candidate_features = load_features(session, candidate.object_id)
    family = PackageFamily.load(session, repo.id)

    # Only the incident's *failing* subject authorises. Accepting any mention -
    # a dependency it lists, a package it explicitly clears, one whose role it
    # never settles - read "I run this package" as "I confirm it is what broke",
    # which are different statements. It also meant a package the report had
    # declared healthy could authorise acting on that same report.
    failing = candidate_features.subject_packages
    if stated & failing or family.any_related(stated, failing):
        return Authorization(authorized=True, source="structured_package")

    mentioned = (
        candidate_features.subject_dependencies
        | candidate_features.subject_confirmed_non_primary
        | candidate_features.subject_conflicted
        | candidate_features.subject_unresolved
    )
    if stated & mentioned or family.any_related(stated, mentioned):
        reason = (
            f"this incident mentions {sorted(stated & mentioned)[:3] or sorted(stated)[:3]}, but "
            "not as what failed - running a package an incident happens to name does not make "
            "that incident yours"
        )
    else:
        reason = f"you named {sorted(stated)[:3]}, which this incident does not mention"
    return Authorization(
        authorized=False,
        requires_confirmation=True,
        missing=[
            f"{reason}; if the reading below is right anyway: `--confirm {understood.digest}`"
        ],
    )


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

    # Stored features and query features must come from the same extractor,
    # or every comparison below is meaningless. This raises rather than
    # degrading: a wrong answer is worse than a refusal to answer.
    require_fresh_signatures(session, repo)

    fp = fingerprint(request.error, extra_context=request.question)
    query_features = feat.extract(chr(10).join(p for p in (request.error, request.question) if p))
    debug.fingerprint = fp.to_json()
    debug.features = query_features.to_json()

    base = DiagnosisResponse(
        status="insufficient_evidence",
        environment=environment,
        recommended_action=RecommendedAction(type="abstain"),
        data_as_of=data_as_of,
        sync_health=sync_health,
        coverage_notes=coverage_notes,
        fingerprint=fp.to_json(),
    )

    if fp.is_empty and query_features.is_empty():
        base.recommended_action = RecommendedAction(
            type="collect_more_info", rationale="no error text or question was supplied"
        )
        base.missing_information = ["the exact error message or symptom"]
        return base, packet, debug

    report_assessment = assess_report(request, query_features)
    base.report_assessment = report_assessment
    if not report_assessment.retrieval_allowed:
        if report_assessment.kind in {"question", "idea"}:
            base.recommended_action = RecommendedAction(
                type="abstain", rationale=report_assessment.rationale
            )
        else:
            base.recommended_action = RecommendedAction(
                type="collect_more_info",
                rationale=(
                    "no incident was proposed because the input is not yet evidenced as an "
                    "observed failure outside quoted material"
                ),
            )
        base.missing_information = [
            "one concrete observed symptom or error from the current environment, "
            "or an explicit `--report-kind failure` declaration if the report is genuine "
            "but lacks a machine-readable witness"
        ]
        return base, packet, debug

    # --- stage 1: retrieved_candidate, stage 2: accepted_same_incident -----
    outcome = pipeline.retrieve_and_identify(
        session,
        repo_id=repo.id,
        fingerprint=fp,
        features=query_features,
        anchors=tuple(request.identity_anchors),
    )
    debug.retrieval = outcome.to_json()

    rejected_by_class: dict[str, int] = {}
    for rejection in outcome.rejections:
        key = rejection.get("rejection") or "not_evaluated"
        rejected_by_class[key] = rejected_by_class.get(key, 0) + 1

    base.stages = StageReport(
        retrieved_candidates=len(outcome.candidates),
        accepted_same_incident=outcome.accepted is not None,
        actionable_incident=False,
        rejected_candidates=rejected_by_class,
        stopped_at="retrieved_candidate",
    )

    if outcome.accepted is None:
        # Candidates may exist; none of them is the same problem. Nothing about
        # them reaches the public output - counts only, no identity.
        why = ""
        if "different_root_cause" in rejected_by_class:
            why = (
                "; candidates were rejected because this report states a different "
                "failure mechanism"
            )
        elif rejected_by_class:
            why = "; candidates shared wording but not identifying evidence"
        base.recommended_action = RecommendedAction(
            type="abstain",
            rationale=("no stored incident is the same problem as this report" + why),
        )
        base.missing_information = [
            "whether this symptom has been reported upstream for this repository",
        ]
        if outcome.notes:
            base.coverage_notes = [*base.coverage_notes, *outcome.notes]
        if sync_health != "complete":
            base.missing_information.append(
                "coverage is partial, so absence of a match is not proof of absence"
            )
        return base, packet, debug

    top = outcome.accepted
    symptom_obj = session.get(SourceObject, top.object_id)
    if symptom_obj is None:  # pragma: no cover - retrieval returned a stale id
        base.recommended_action = RecommendedAction(
            type="abstain",
            rationale="the matched thread is no longer present in the local store",
        )
        base.missing_information = ["re-run `rt sync` so the evidence store is current"]
        return base, packet, debug
    symptom_item = ev.from_source_object(session, symptom_obj, role="symptom")
    symptom_id = packet.add(symptom_item)

    identity = top.identity
    incident = IncidentSummary(
        matched=True,
        title=symptom_obj.title,
        url=symptom_obj.url,
        symptom_signature=fp.signature[:400] or None,
        matched_tokens=top.token_matched[:10],
        score=round(identity.score, 2) if identity else 0.0,
        resolution_signal=symptom_obj.state,
        identity_rule=identity.rule if identity else None,
        shared_features={k: v[:6] for k, v in (identity.shared if identity else {}).items() if v},
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
        change = resolve_linked_change(session, repo, symptom_obj, git)
        if change is None:
            change = resolve_change(git, releases, symptom_tokens, symptom_text=symptom_text)
        debug.change = change.to_json() if change else None
        if change is not None:
            # `persist=False` means read-only, and that has to include the
            # containment cache. Refreshing it here rewrote `computed_at` and
            # the evidence transcript on every diagnosis, so a tool documented
            # as read-only left a trace in the database on each call.
            containment = compute_containment(
                session, repo, change.commit_sha, git=git, persist=persist
            )
            debug.containment = containment.to_json()
            release_obj = _release_by_tag(releases, containment.first_release_containing)

    change_ids: list[str] = []
    release_ids: list[str] = []
    if change is not None:
        if change.source_object_id is not None:
            change_obj = session.get(SourceObject, change.source_object_id)
            if change_obj is not None:
                change_ids.append(
                    packet.add(ev.from_source_object(session, change_obj, role="change"))
                )
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
            confidence="high" if (identity and identity.score >= 12) else "medium",
            basis="observed",
            evidence_ids=[symptom_id],
        )
    ]

    if change is not None and change_ids:
        if change.derivation == "github_native":
            value = (
                f"pull request {change.evidence.get('pull_request')} closes this issue and "
                f"was merged as commit {change.short_sha} ({change.subject})"
            )
            confidence: Literal["high", "medium", "low"] = "high"
            basis: Literal["explicit", "deterministic", "observed", "inferred"] = "explicit"
        else:
            value = (
                f"commit {change.short_sha} ({change.subject}) touches "
                f"{', '.join(change.files[:3])} and is the most likely related change"
            )
            confidence = "medium"
            basis = "inferred"
        claims.append(
            Claim(
                type="change",
                value=value,
                confidence=confidence,
                basis=basis,
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
        status = "probable" if (identity and identity.score >= 12) else "insufficient_evidence"
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

    # --- stage 3 needs an authorisation source ------------------------------
    #
    # Retrieval and identity run on free text; recommending that someone change
    # what they run does not. A misreading of prose that can only *find* a
    # candidate produces a wrong suggestion, which the reader sees and rejects.
    # One that can authorise an action produces a wrong action. So the two are
    # separated: the packages the user stated as fields decide whether this
    # answer may propose a change.
    understood = _understanding(
        request,
        query_features,
        incident,
        action,
        report_assessment,
        identity=identity,
        evidence=[f"{item.source_type}:{item.locator}" for item in packet.items.values()],
    )
    authorization = _authorize(session, request, top, repo, understood)
    if action.type in DiagnosisResponse.VERSION_ACTIONS and not authorization.authorized:
        authorization.proposed_action = action.type
        authorization.proposed_target = action.target
        action = RecommendedAction(
            type="collect_more_info",
            rationale=(
                f"this looks like the same incident, and {action.rationale} - but this is a "
                "proposal rather than a recommendation until you say it is your situation: "
                "check the reading in `understood` and confirm it, or name the package you "
                "are running"
            ),
            confidence="low",
            evidence_ids=action.evidence_ids,
        )
        if status == "probable":
            # Not "confirmed" and not a refusal: the incident is a real match,
            # but nothing authorises acting on it yet.
            status = "insufficient_evidence"

    stages = StageReport(
        retrieved_candidates=len(outcome.candidates),
        accepted_same_incident=True,
        actionable_incident=action.type in DiagnosisResponse.VERSION_ACTIONS,
        rejected_candidates=rejected_by_class,
        stopped_at=(
            "actionable_incident"
            if action.type in DiagnosisResponse.VERSION_ACTIONS
            else "accepted_same_incident"
        ),
    )

    response = DiagnosisResponse(
        status=status,  # type: ignore[arg-type]
        environment=environment,
        stages=stages,
        authorization=authorization,
        report_assessment=report_assessment,
        understood=understood,
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
