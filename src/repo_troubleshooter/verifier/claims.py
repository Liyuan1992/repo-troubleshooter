"""Post-synthesis verification.

Nothing leaves the system on trust. Every claim is re-checked against the
database and against git: the evidence must exist in the packet, resolve to a
real object, and - for release and change claims - the tag or commit must
actually be there. Claims that fail are dropped, and an action whose supporting
claims were dropped is downgraded rather than shipped unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.diagnosis.contract import Claim, DiagnosisResponse
from repo_troubleshooter.evidence.packet import EvidencePacket
from repo_troubleshooter.store.models import Release, Repository


@dataclass
class VerificationReport:
    checked: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    downgraded: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.dropped and not self.errors

    def to_json(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "dropped": self.dropped,
            "downgraded": self.downgraded,
            "errors": self.errors,
        }


def _release_exists(session: Session, repo: Repository, tag: str) -> bool:
    return (
        session.scalar(
            select(Release.id).where(Release.repo_id == repo.id, Release.tag_name == tag)
        )
        is not None
    )


def verify(
    response: DiagnosisResponse,
    packet: EvidencePacket,
    session: Session,
    repo: Repository,
    git: GitRepo | None = None,
) -> VerificationReport:
    """Mutates ``response`` in place, removing anything it cannot stand behind."""
    report = VerificationReport()
    survivors: list[Claim] = []

    for claim in response.claims:
        report.checked += 1

        if not claim.evidence_ids:
            report.dropped.append(
                {"claim": claim.value, "type": claim.type, "reason": "no evidence cited"}
            )
            continue

        missing = [eid for eid in claim.evidence_ids if not packet.has(eid)]
        if missing:
            report.dropped.append(
                {
                    "claim": claim.value,
                    "type": claim.type,
                    "reason": f"evidence not in packet: {', '.join(missing)}",
                }
            )
            continue

        bad_release = False
        for eid in claim.evidence_ids:
            item = packet.items[eid]
            if item.source_type == "release" and not _release_exists(session, repo, item.locator):
                report.dropped.append(
                    {
                        "claim": claim.value,
                        "type": claim.type,
                        "reason": f"release {item.locator} does not exist in the store",
                    }
                )
                bad_release = True
                break
            if item.source_type == "commit" and git is not None:
                if not git.commit_exists(item.locator):
                    report.dropped.append(
                        {
                            "claim": claim.value,
                            "type": claim.type,
                            "reason": f"commit {item.locator} not present in the mirror",
                        }
                    )
                    bad_release = True
                    break
        if bad_release:
            continue

        survivors.append(claim)

    response.claims = survivors

    # The verifier may withdraw the whole incident, not merely a citation: if the
    # symptom claim did not survive, there is nothing left that says "same problem".
    if response.incident.matched and not any(c.type == "symptom_match" for c in survivors):
        reason = "the symptom match did not verify, so no incident is claimed"
        report.dropped.append({"claim": "incident", "type": "incident", "reason": reason})
        response.revoke_incident(reason)
        return report

    # An action must still be backed by surviving evidence.
    action = response.recommended_action
    if action.evidence_ids:
        surviving_ids = {eid for claim in survivors for eid in claim.evidence_ids}
        unsupported = [eid for eid in action.evidence_ids if eid not in surviving_ids]
        if unsupported and action.type in ("upgrade", "downgrade", "migrate", "config_change"):
            report.downgraded.append(
                {
                    "action": action.type,
                    "target": action.target,
                    "reason": f"supporting claims were dropped: {', '.join(unsupported)}",
                }
            )
            action.type = "collect_more_info"
            action.target = None
            action.confidence = "low"
            action.rationale = (
                "the evidence behind the original recommendation did not verify; "
                "more information is needed"
            )
            response.status = "insufficient_evidence"

    if action.type in ("upgrade", "downgrade") and action.target:
        if not _release_exists(session, repo, action.target):
            report.downgraded.append(
                {
                    "action": action.type,
                    "target": action.target,
                    "reason": "target release does not exist in the store",
                }
            )
            action.type = "collect_more_info"
            action.target = None
            action.confidence = "low"
            response.status = "insufficient_evidence"

    return report
