"""Stage 1 and stage 2 of the retrieval contract.

    retrieved_candidate  ->  accepted_same_incident  ->  actionable_incident
         (this module)          (this module)              (diagnosis engine)

Stage 1 casts a deliberately wider net than before, through two channels:

* **tokens** - exact/lexical matching on structural tokens. Precise, but blind
  to paraphrase: a user who never pastes the stack trace matches nothing.
* **signatures** - the mined feature classes of each stored incident, so
  "the boot graph has no entries" can reach the thread that said
  "``__DSH_BOOT__`` has zero entries and zero batches".

Widening stage 1 is only safe because stage 2 is strict and separate: a
candidate is not a match, and only stage 2 may set ``incident.matched``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from repo_troubleshooter.fingerprint.error import ErrorFingerprint
from repo_troubleshooter.fingerprint.features import SymptomFeatures
from repo_troubleshooter.relations import signatures
from repo_troubleshooter.retrieval import candidates as token_channel
from repo_troubleshooter.retrieval.identity import IdentityVerdict, evaluate

MAX_CANDIDATES = 8
MAX_IDENTITY_CHECKS = 6
# Feature classes that may pull a candidate into stage 1 on their own.
CANDIDATE_FEATURE_KINDS = (
    "subject_package",
    "subject_path",
    "subject_module",
    "error",
    "structural",
    "behavior",
)
MIN_FEATURE_HITS = 2

_FEATURE_SQL = """
WITH q(kind, value) AS (
    SELECT * FROM unnest(CAST(:kinds AS text[]), CAST(:values AS text[]))
)
SELECT s.object_id, s.feature_kind, s.feature_value
  FROM symptom_signature s
  JOIN q ON q.kind = s.feature_kind AND q.value = s.feature_value
 WHERE s.repo_id = :repo_id
"""

_OBJECT_SQL = """
SELECT id, kind, number, native_id, title, url, state, category, parent_id
  FROM source_object
 WHERE id = ANY(CAST(:ids AS int[]))
"""

_CORPUS_SQL = "SELECT count(DISTINCT object_id) FROM symptom_signature WHERE repo_id = :repo_id"


@dataclass
class RetrievedCandidate:
    """Stage 1 output. Worth checking - nothing more."""

    object_id: int
    kind: str
    number: int | None
    title: str | None
    url: str | None
    state: str | None
    channels: list[str] = field(default_factory=list)
    token_score: float = 0.0
    token_matched: list[str] = field(default_factory=list)
    feature_hits: dict[str, list[str]] = field(default_factory=dict)
    identity: IdentityVerdict | None = None

    @property
    def feature_hit_count(self) -> int:
        return sum(len(v) for v in self.feature_hits.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "kind": self.kind,
            "number": self.number,
            "title": self.title,
            "channels": self.channels,
            "token_score": round(self.token_score, 2),
            "token_matched": self.token_matched[:8],
            "feature_hits": self.feature_hits,
            "identity": self.identity.to_json() if self.identity else None,
        }


@dataclass
class RetrievalOutcome:
    """Everything the trace needs, and exactly one accepted incident (or none)."""

    candidates: list[RetrievedCandidate] = field(default_factory=list)
    accepted: RetrievedCandidate | None = None
    token_threshold: dict[str, float] = field(default_factory=dict)
    corpus_objects: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def rejections(self) -> list[dict[str, Any]]:
        return [
            {
                "object_id": c.object_id,
                "number": c.number,
                "title": c.title,
                "rejection": c.identity.rejection if c.identity else "not_evaluated",
                "score": round(c.identity.score, 2) if c.identity else None,
                "shared": c.identity.shared if c.identity else {},
            }
            for c in self.candidates
            if c is not self.accepted
        ]

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": "retrieved_candidate -> accepted_same_incident",
            "corpus_objects": self.corpus_objects,
            "token_threshold": self.token_threshold,
            "candidate_count": len(self.candidates),
            "candidates": [c.to_json() for c in self.candidates],
            "accepted": self.accepted.to_json() if self.accepted else None,
            "rejections": self.rejections,
            "notes": self.notes,
        }


def _feature_channel(
    session: Session, repo_id: int, features: SymptomFeatures
) -> dict[int, dict[str, list[str]]]:
    pairs: list[tuple[str, str]] = []
    for kind, values in (
        ("subject_package", features.subject_packages),
        ("subject_path", features.subject_paths),
        ("subject_module", features.subject_modules),
        ("error", features.error),
        ("structural", features.structural),
        ("behavior", features.behavior),
    ):
        if kind in CANDIDATE_FEATURE_KINDS:
            pairs.extend((kind, value) for value in values)
    if not pairs:
        return {}

    rows = session.execute(
        sql_text(_FEATURE_SQL),
        {
            "kinds": [kind for kind, _ in pairs],
            "values": [value for _, value in pairs],
            "repo_id": repo_id,
        },
    ).all()

    hits: dict[int, dict[str, list[str]]] = {}
    for object_id, kind, value in rows:
        hits.setdefault(object_id, {}).setdefault(kind, []).append(value)
    return hits


def retrieve(
    session: Session,
    *,
    repo_id: int,
    fingerprint: ErrorFingerprint,
    features: SymptomFeatures,
    limit: int = MAX_CANDIDATES,
) -> RetrievalOutcome:
    """Stage 1: gather candidates from both channels. No identity decision here."""
    outcome = RetrievalOutcome()

    token_result = token_channel.search(
        session, repo_id=repo_id, fingerprint=fingerprint, limit=limit
    )
    outcome.token_threshold = token_result.threshold

    outcome.corpus_objects = (
        session.execute(sql_text(_CORPUS_SQL), {"repo_id": repo_id}).scalar() or 0
    )

    merged: dict[int, RetrievedCandidate] = {}
    for hit in token_result.hits:
        merged[hit.object_id] = RetrievedCandidate(
            object_id=hit.object_id,
            kind=hit.kind,
            number=hit.number,
            title=hit.title,
            url=hit.url,
            state=hit.state,
            channels=["tokens"],
            token_score=hit.score,
            token_matched=hit.matched_tokens,
        )

    feature_hits = _feature_channel(session, repo_id, features)
    unknown_ids = [oid for oid in feature_hits if oid not in merged]
    meta: dict[int, Any] = {}
    if unknown_ids:
        for meta_row in session.execute(sql_text(_OBJECT_SQL), {"ids": unknown_ids}).all():
            meta[meta_row[0]] = meta_row

    for object_id, hits in feature_hits.items():
        strong_hits = sum(
            len(v)
            for k, v in hits.items()
            if k in ("subject_package", "subject_path", "error", "structural")
        )
        total_hits = sum(len(v) for v in hits.values())
        if object_id in merged:
            merged[object_id].channels.append("signatures")
            merged[object_id].feature_hits = hits
            continue
        # A single shared feature is noise; require a small profile to enter stage 1.
        if total_hits < MIN_FEATURE_HITS and strong_hits == 0:
            continue
        row: Any = meta.get(object_id)
        if row is None:
            continue
        merged[object_id] = RetrievedCandidate(
            object_id=object_id,
            kind=row[1],
            number=row[2],
            title=row[4],
            url=row[5],
            state=row[6],
            channels=["signatures"],
            feature_hits=hits,
        )

    ordered = sorted(
        merged.values(),
        key=lambda c: (-(c.token_score), -c.feature_hit_count, c.object_id),
    )
    outcome.candidates = ordered[:limit]
    if not outcome.candidates:
        best = token_result.rejected[0] if token_result.rejected else None
        if best is not None:
            outcome.notes.append(
                f"closest token candidate rejected because {best.rejection_reason}"
            )
    return outcome


def identify(
    session: Session,
    *,
    repo_id: int,
    query_features: SymptomFeatures,
    outcome: RetrievalOutcome,
    max_checks: int = MAX_IDENTITY_CHECKS,
) -> RetrievalOutcome:
    """Stage 2: decide which candidate, if any, is the *same incident*."""
    query_values = (
        query_features.identifying_subjects
        | query_features.error
        | query_features.structural
        | query_features.behavior
        | query_features.component
    )
    doc_freq = signatures.document_frequencies(session, repo_id, query_values)

    for candidate in outcome.candidates[:max_checks]:
        candidate_features = signatures.load_features(session, candidate.object_id)
        candidate.identity = evaluate(
            query_features,
            candidate_features,
            doc_freq=doc_freq,
            corpus_objects=outcome.corpus_objects,
        )

    accepted = [c for c in outcome.candidates if c.identity is not None and c.identity.accepted]
    if accepted:
        accepted.sort(key=lambda c: -(c.identity.score if c.identity else 0.0))
        outcome.accepted = accepted[0]
    return outcome


def retrieve_and_identify(
    session: Session,
    *,
    repo_id: int,
    fingerprint: ErrorFingerprint,
    features: SymptomFeatures,
    limit: int = MAX_CANDIDATES,
) -> RetrievalOutcome:
    outcome = retrieve(
        session, repo_id=repo_id, fingerprint=fingerprint, features=features, limit=limit
    )
    return identify(session, repo_id=repo_id, query_features=features, outcome=outcome)
