"""Sampled holdout over real upstream reports.

Why this exists
---------------

Acceptance used to be judged on adversarial inputs: a reviewer wrote a sentence
shape the reader had not seen, and every one that got through was a defect. Each
of those findings was real, and the supply of them is unbounded - the generator
is a person writing English, the reader is shallow parsing. Judged that way,
there is no state in which the tool is finished.

So the criterion moved to the population the tool is actually for: **reports
people really wrote**. This script samples them out of the synced corpus, asks
the engine about each one with that report removed from the evidence, and counts
how often it would propose acting on some *other* incident.

Leave-one-out is what makes the question meaningful. With the report still in
the corpus it matches itself, scores highest, and nothing is learned. Removed,
the honest answer for most reports is "no incident here that I know of", and
anything else is either a genuine duplicate or a false identity.

What the number means, and does not
-----------------------------------

It is measured at the *proposal* level - `authorization.proposed_action` - not at
the recommendation level. Nothing here states a package or confirms a reading, so
by construction no action would be authorised; measuring what the gate blocked
would only measure the gate. The proposal is where identity quality is visible.

It is an **upper bound on error**. Two real reports can be the same incident, and
a proposal that points at a duplicate is right, not wrong. The pairs are printed
so they can be read rather than assumed.

Usage
-----

    uv run python evals/holdout.py [--sample 60] [--seed 20260904]
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.diagnosis.contract import DiagnosisRequest, DiagnosisResponse
from repo_troubleshooter.diagnosis.engine import diagnose
from repo_troubleshooter.relations.signatures import object_text
from repo_troubleshooter.store.db import session_scope
from repo_troubleshooter.store.models import Repository, SourceObject, SymptomSignature

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

#: The environment every sampled report is asked about. A holdout report rarely
#: states its version in a field, and the version gate needs one to reach a
#: proposal at all - so one is supplied, and stated here rather than hidden.
ASSUMED_VERSION = "0.1.2-alpha.1"

#: Below this a "report" is a one-line comment or a title with no body, and
#: there is nothing to identify it by.
MIN_TEXT_CHARS = 200

#: The acceptance threshold, from `docs/threat_model.md`. Exceeding it fails the
#: run: the criterion is a gate, not a number in a report someone may read.
#: Read it next to `proposal_opportunity_count` - passing with no opportunities
#: means nothing could have proposed, not that nothing wrong was proposed.
#: There is deliberately no threshold on the match rate: one sample of one
#: repository is not a basis for setting one, and part of that number is real
#: duplicates, which only a person can separate out.
MAX_FALSE_PROPOSAL_RATE = 0.05


@dataclass
class HoldoutCase:
    object_id: int
    number: int | None
    title: str
    matched_url: str | None
    matched_object_id: int | None
    proposed_action: str | None
    proposed_target: str | None
    status: str
    stopped_at: str
    #: True when the matched incident carries a released fix to point at, so a
    #: version action was reachable at all. Without this the proposal rate has
    #: no denominator: a run where nothing could ever propose scores zero for
    #: the wrong reason.
    proposal_possible: bool = False

    @property
    def proposes_change(self) -> bool:
        return self.proposed_action in DiagnosisResponse.VERSION_ACTIONS

    def to_json(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "proposal_possible": self.proposal_possible,
            "number": self.number,
            "title": self.title[:120],
            "matched_url": self.matched_url,
            "proposed_action": self.proposed_action,
            "proposed_target": self.proposed_target,
            "status": self.status,
            "stopped_at": self.stopped_at,
        }


@dataclass
class HoldoutResult:
    repo: str
    sample: int
    seed: int
    cases: list[HoldoutCase] = field(default_factory=list)
    control: dict[str, Any] = field(default_factory=dict)

    @property
    def proposals(self) -> list[HoldoutCase]:
        return [c for c in self.cases if c.proposes_change]

    @property
    def identified(self) -> list[HoldoutCase]:
        """Reports where some *other* incident was accepted as the same one."""
        return [c for c in self.cases if c.matched_url]

    @property
    def opportunities(self) -> list[HoldoutCase]:
        """Matches where a version action was reachable at all."""
        return [c for c in self.cases if c.proposal_possible]

    def to_json(self) -> dict[str, Any]:
        proposals = self.proposals
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "repo": self.repo,
            "sample": self.sample,
            "seed": self.seed,
            "assumed_core_version": ASSUMED_VERSION,
            "method": (
                "leave-one-out: the sampled report is removed from the evidence store inside a "
                "transaction that is rolled back, then its own text is asked about"
            ),
            "other_report_match_rate": {
                "value": round(len(self.identified) / self.sample, 4) if self.sample else 0.0,
                "n": self.sample,
                "definition": (
                    "share of real reports where, with the report itself removed, some other "
                    "report was accepted as the same incident. Machine-counted and nothing "
                    "more: some of these are real duplicates, where matching is correct. "
                    "Calling this a false-identity rate would count them as errors. The "
                    "adjudicated split is in evals/holdout_judgement.md and cannot be "
                    "computed here - whether two reports are the same incident is a reading"
                ),
            },
            "identified": [c.to_json() for c in self.identified],
            "proposal_opportunity_count": {
                "value": len(self.opportunities),
                "definition": (
                    "matches where the incident carried a released fix, so a version action "
                    "was reachable. The denominator of the rate below: at zero, the rate "
                    "describes an absence of opportunity rather than an absence of error"
                ),
            },
            "positive_control": self.control,
            "false_proposal_count": len(proposals),
            "false_proposal_rate": {
                "value": round(len(proposals) / self.sample, 4) if self.sample else 0.0,
                "n": self.sample,
                "definition": (
                    "share of sampled reports that would propose a version action pointing at "
                    "another incident. Read it next to proposal_opportunity_count: zero out of "
                    "zero opportunities is an observation, not evidence"
                ),
            },
            "proposals": [c.to_json() for c in proposals],
            "cases": [c.to_json() for c in self.cases],
        }


#: The one report in this corpus known to be a released, fixed incident, used
#: as the positive control below.
CONTROL_SYMPTOM = (
    "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; "
    "client-modules reports HTML did not preload "
    "@deepseek-ai/dsh-client-modules/client.js, and the host throws "
    "TypeError: e.indexOf is not a function"
)


def _positive_control(session: Session, repo: Repository) -> dict[str, Any]:
    """Prove this measurement can see a proposal at all.

    A false-proposal rate of zero is only a measurement if a non-zero one was
    reachable by the same path. So the same call is made once with a report
    known to be a released, fixed incident: if *that* does not reach a proposal,
    the zero above is describing the harness, not the engine.
    """
    response, _packet, _debug = diagnose(
        DiagnosisRequest(repo=repo.full_name, error=CONTROL_SYMPTOM, core_version=ASSUMED_VERSION),
        session,
        persist=False,
    )
    proposed = response.authorization.proposed_action or (
        response.recommended_action.type
        if response.recommended_action.type in DiagnosisResponse.VERSION_ACTIONS
        else None
    )
    return {
        "matched": response.incident.matched,
        "proposed_action": proposed,
        "proposed_target": response.authorization.proposed_target
        or response.recommended_action.target,
        "reaches_a_proposal": proposed in DiagnosisResponse.VERSION_ACTIONS,
        "definition": (
            "a known released incident asked the same way as the sample. It must reach a "
            "proposal, or the zero above says nothing about the engine"
        ),
    }


def _sample_objects(session: Session, repo: Repository, size: int, seed: int) -> list[SourceObject]:
    """Deterministically sample discussions that have enough text to identify."""
    rows = list(
        session.scalars(
            select(SourceObject)
            .where(
                SourceObject.repo_id == repo.id,
                SourceObject.kind == "discussion",
                SourceObject.parent_id.is_(None),
            )
            .order_by(SourceObject.id)
        )
    )
    # Eligibility is measured on the body alone, deliberately. This decides
    # *which* reports are sampled, so changing it changes the population being
    # measured; only the query changed in this revision, and the number stays
    # comparable with the previous one.
    usable = [row for row in rows if len(object_text(session, row.id) or "") >= MIN_TEXT_CHARS]
    rng = random.Random(seed)  # noqa: S311 - sampling a corpus, not a secret
    rng.shuffle(usable)
    return usable[:size]


def _has_release_evidence(response: DiagnosisResponse) -> bool:
    """Did the matched incident carry a released fix to point at?

    Without one the version gate stops the answer before any action is
    considered, so no proposal was ever reachable and a zero proposal rate says
    nothing about identity.
    """
    return any(ref.source_type == "release" for ref in response.evidence)


def _ask_without(session: Session, repo: Repository, obj: SourceObject) -> HoldoutCase:
    """Ask about one report with that report taken out of the evidence.

    The deletion happens inside a savepoint that is always rolled back, so the
    corpus this measurement runs against is the same one it started with.
    """
    # Title *and* body, because that is what `features_for_object` mines into
    # the corpus. Asking with the body alone made the query systematically
    # poorer than the rows it was being compared against - a methodological
    # asymmetry of my own making, not a property of the engine.
    title = obj.title or ""
    text = f"{title}\n{object_text(session, obj.id)}"
    number = obj.number
    object_id = obj.id

    savepoint = session.begin_nested()
    try:
        session.query(SymptomSignature).filter(SymptomSignature.object_id == object_id).delete(
            synchronize_session=False
        )
        session.query(SourceObject).filter(SourceObject.id == object_id).delete(
            synchronize_session=False
        )
        session.flush()

        response, _packet, _debug = diagnose(
            DiagnosisRequest(repo=repo.full_name, error=text, core_version=ASSUMED_VERSION),
            session,
            persist=False,
        )
    finally:
        savepoint.rollback()

    authorization = response.authorization
    proposed = authorization.proposed_action or (
        response.recommended_action.type
        if response.recommended_action.type in DiagnosisResponse.VERSION_ACTIONS
        else None
    )
    return HoldoutCase(
        proposal_possible=bool(response.incident.matched and _has_release_evidence(response)),
        object_id=object_id,
        number=number,
        title=title,
        matched_url=response.incident.url,
        matched_object_id=None,
        proposed_action=proposed,
        proposed_target=authorization.proposed_target or response.recommended_action.target,
        status=response.status,
        stopped_at=response.stages.stopped_at,
    )


def run(sample: int, seed: int) -> HoldoutResult:
    with session_scope() as session:
        repo = session.scalar(select(Repository))
        if repo is None:
            raise SystemExit("no repository synced; run `rt sync` first")
        objects = _sample_objects(session, repo, sample, seed)
        result = HoldoutResult(repo=repo.full_name, sample=len(objects), seed=seed)
        result.control = _positive_control(session, repo)
        for index, obj in enumerate(objects, start=1):
            result.cases.append(_ask_without(session, repo, obj))
            if index % 10 == 0:
                print(f"  {index}/{len(objects)} reports asked")
        session.rollback()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260904)
    args = parser.parse_args()

    result = run(args.sample, args.seed)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "holdout.json"
    payload = result.to_json()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"{len(result.identified)}/{result.sample} real reports matched another report; "
        f"{len(result.opportunities)} of those could reach a version action; "
        f"{len(result.proposals)} would"
    )
    print(f"  other_report_match_rate    = {payload['other_report_match_rate']['value']}")
    print(f"  proposal_opportunity_count = {payload['proposal_opportunity_count']['value']}")
    print(f"  false_proposal_rate       = {payload['false_proposal_rate']['value']}")
    print(f"  written to {path}")
    for case in result.identified:
        marker = "PROPOSES" if case.proposes_change else "matched "
        print(f"  {marker} #{case.number} {case.title[:64]!r} -> {case.matched_url}")

    control = payload["positive_control"]
    print(
        f"  positive_control          = "
        f"{'reaches a proposal' if control['reaches_a_proposal'] else 'DOES NOT REACH ONE'}"
    )
    if not control["reaches_a_proposal"]:
        print(
            "\nFAIL: the positive control did not reach a proposal, so a zero "
            "false_proposal_rate would describe this harness rather than the engine"
        )
        return 1

    measured = payload["false_proposal_rate"]["value"]
    if measured > MAX_FALSE_PROPOSAL_RATE:
        print(
            f"\nFAIL: false_proposal_rate {measured} exceeds {MAX_FALSE_PROPOSAL_RATE} "
            f"(docs/threat_model.md)"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
