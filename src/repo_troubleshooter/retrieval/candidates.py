"""Exact + lexical candidate retrieval with a real rejection threshold.

Deterministic, no embeddings, no model. Two properties matter more than recall:

* **Discriminative tokens decide, not text similarity.** Every candidate must
  share specific tokens (symbols, packages, error codes) with the query. Tokens
  are weighted by how rare they are in this repository's corpus, so ``startup``
  or ``config`` cannot carry a match on their own.
* **Returning the best candidate is not enough.** If the best score is weak the
  retriever returns nothing, and the caller must abstain. That is what keeps an
  unrelated PostgreSQL failure from matching a Node loader incident.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from repo_troubleshooter.fingerprint.error import ErrorFingerprint

# Calibrated against the evaluator cases plus the negative-control suite in
# evals/cases. Raising MIN_SCORE trades recall for abstention precision.
MIN_MATCHED_TOKENS = 2
MIN_SCORE = 8.0
MIN_COVERAGE = 0.20
MAX_QUERY_TOKENS = 24
# A token is "identifying" when it names something specific rather than
# describing it: a symbol, package, path, error code - or a word so rare in this
# corpus that it is effectively a name. Common English words ("connection",
# "applying", "startup") can add score but can never carry a match alone.
RARE_DOC_FREQ = 2

_STRUCTURAL_CHARS = "@/_."
_CAMEL_RE = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]+)+$")


def is_structural(token: str) -> bool:
    """Shaped like a name: a symbol, package, path, dotted call or error type."""
    if any(ch in token for ch in _STRUCTURAL_CHARS):
        return True
    if "-" in token and len(token) >= 6:
        return True
    if _CAMEL_RE.match(token) or (token.isupper() and len(token) >= 4):
        return True
    return token.endswith(("error", "exception")) and len(token) > 7


def is_identifying(token: str, doc_freq: int) -> bool:
    """Structural tokens identify; so do words rare enough to act as names."""
    return is_structural(token) or doc_freq <= RARE_DOC_FREQ


@dataclass
class CandidateHit:
    object_id: int
    kind: str
    number: int | None
    native_id: str
    title: str | None
    url: str | None
    state: str | None
    category: str | None
    score: float
    coverage: float
    matched_tokens: list[str] = field(default_factory=list)
    identifying_tokens: list[str] = field(default_factory=list)
    structural_tokens: list[str] = field(default_factory=list)
    unit_hits: int = 0
    excerpt: str | None = None
    rejection_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "kind": self.kind,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "score": round(self.score, 3),
            "coverage": round(self.coverage, 3),
            "matched_tokens": self.matched_tokens,
            "identifying_tokens": self.identifying_tokens,
            "structural_tokens": self.structural_tokens,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class RetrievalResult:
    hits: list[CandidateHit]
    rejected: list[CandidateHit]
    query_tokens: list[str]
    token_weights: dict[str, float]
    corpus_objects: int
    threshold: dict[str, float]

    @property
    def accepted(self) -> bool:
        return bool(self.hits)

    def to_json(self) -> dict[str, Any]:
        return {
            "query_tokens": self.query_tokens,
            "corpus_objects": self.corpus_objects,
            "threshold": self.threshold,
            "hits": [h.to_json() for h in self.hits],
            "rejected_best": [h.to_json() for h in self.rejected[:3]],
        }


_SEARCH_SQL = """
WITH q(token) AS (SELECT unnest(CAST(:tokens AS text[]))),
hits AS (
    SELECT cu.object_id,
           q.token,
           count(*) AS unit_hits
      FROM content_unit cu
      JOIN q ON cu.text ILIKE '%%' || q.token || '%%'
     WHERE cu.repo_id = :repo_id
     GROUP BY cu.object_id, q.token
),
df AS (
    SELECT token, count(DISTINCT object_id) AS doc_freq
      FROM hits
     GROUP BY token
)
SELECT h.object_id,
       h.token,
       h.unit_hits,
       d.doc_freq
  FROM hits h
  JOIN df d ON d.token = h.token
"""

_OBJECT_SQL = """
SELECT so.id,
       so.kind,
       so.number,
       so.native_id,
       so.title,
       so.url,
       so.state,
       so.category,
       so.parent_id
  FROM source_object so
 WHERE so.id = ANY(CAST(:ids AS int[]))
"""

_EXCERPT_SQL = """
SELECT cu.text
  FROM content_unit cu
 WHERE cu.object_id = :object_id
   AND cu.text ILIKE '%%' || :token || '%%'
 ORDER BY CASE cu.unit_type WHEN 'log' THEN 0 WHEN 'code' THEN 1 ELSE 2 END, length(cu.text)
 LIMIT 1
"""


def _select_query_tokens(fp: ErrorFingerprint) -> list[str]:
    """Prefer the most structural tokens; cap the list so the scan stays cheap."""

    def rank(token: str) -> tuple[int, int]:
        structural = 0 if any(ch in token for ch in "@/_.-") else 1
        return (structural, -len(token))

    unique = list(dict.fromkeys(t for t in fp.discriminative if len(t) >= 4))
    unique.sort(key=rank)
    return unique[:MAX_QUERY_TOKENS]


def search(
    session: Session,
    *,
    repo_id: int,
    fingerprint: ErrorFingerprint,
    limit: int = 5,
    kinds: tuple[str, ...] = ("discussion", "release"),
) -> RetrievalResult:
    """Score candidate objects for one fingerprint, then apply the threshold."""
    tokens = _select_query_tokens(fingerprint)
    empty = RetrievalResult(
        hits=[],
        rejected=[],
        query_tokens=tokens,
        token_weights={},
        corpus_objects=0,
        threshold={
            "min_score": MIN_SCORE,
            "min_matched": MIN_MATCHED_TOKENS,
            "min_coverage": MIN_COVERAGE,
        },
    )
    if not tokens:
        return empty

    corpus_objects = (
        session.execute(
            sql_text("SELECT count(DISTINCT object_id) FROM content_unit WHERE repo_id = :repo_id"),
            {"repo_id": repo_id},
        ).scalar()
        or 0
    )
    if corpus_objects == 0:
        return empty

    rows = session.execute(sql_text(_SEARCH_SQL), {"tokens": tokens, "repo_id": repo_id}).all()
    if not rows:
        return empty

    # Rarity weighting: a token found in most objects proves almost nothing.
    weights: dict[str, float] = {}
    doc_freqs: dict[str, int] = {}
    for _, token, _, doc_freq in rows:
        if token not in weights:
            weights[token] = math.log(1.0 + corpus_objects / max(1, doc_freq))
            doc_freqs[token] = doc_freq

    per_object: dict[int, dict[str, Any]] = {}
    for object_id, token, unit_hits, _ in rows:
        entry = per_object.setdefault(object_id, {"tokens": {}, "units": 0})
        entry["tokens"][token] = unit_hits
        entry["units"] += unit_hits

    object_rows = session.execute(sql_text(_OBJECT_SQL), {"ids": list(per_object)}).all()
    meta = {row[0]: row for row in object_rows}

    # Object dedup: a comment's score belongs to its thread, not to itself.
    rolled: dict[int, dict[str, Any]] = {}
    for object_id, entry in per_object.items():
        row = meta.get(object_id)
        if row is None:
            continue
        target_id = row[8] or object_id  # parent_id when this is a comment
        bucket = rolled.setdefault(target_id, {"tokens": {}, "units": 0})
        for token, hits in entry["tokens"].items():
            bucket["tokens"][token] = bucket["tokens"].get(token, 0) + hits
        bucket["units"] += entry["units"]

    missing = [oid for oid in rolled if oid not in meta]
    if missing:
        for row in session.execute(sql_text(_OBJECT_SQL), {"ids": missing}).all():
            meta[row[0]] = row

    total_weight = sum(weights.values()) or 1.0
    scored: list[CandidateHit] = []
    for object_id, bucket in rolled.items():
        row = meta.get(object_id)
        if row is None:
            continue
        kind = row[1]
        if kinds and kind not in kinds:
            continue
        matched = sorted(bucket["tokens"], key=lambda t: -weights.get(t, 0.0))
        identifying = [t for t in matched if is_identifying(t, doc_freqs.get(t, 0))]
        structural = [t for t in matched if is_structural(t)]
        score = sum(weights.get(t, 0.0) for t in matched)
        coverage = score / total_weight
        scored.append(
            CandidateHit(
                object_id=object_id,
                kind=kind,
                number=row[2],
                native_id=row[3],
                title=row[4],
                url=row[5],
                state=row[6],
                category=row[7],
                score=score,
                coverage=coverage,
                matched_tokens=matched,
                identifying_tokens=identifying,
                structural_tokens=structural,
                unit_hits=bucket["units"],
            )
        )

    scored.sort(key=lambda h: (-h.score, -h.unit_hits, h.object_id))

    accepted: list[CandidateHit] = []
    rejected: list[CandidateHit] = []
    for hit in scored:
        if not hit.identifying_tokens:
            hit.rejection_reason = "no identifying token matched (only generic words)"
        elif not hit.structural_tokens and len(hit.identifying_tokens) < 2:
            # One rare English word is a coincidence, not an identity match.
            hit.rejection_reason = (
                f"only one rare word matched ({hit.identifying_tokens[0]!r}) "
                "and no symbol, package or error type"
            )
        elif len(hit.matched_tokens) < MIN_MATCHED_TOKENS:
            hit.rejection_reason = f"only {len(hit.matched_tokens)} token(s) matched"
        elif hit.score < MIN_SCORE:
            hit.rejection_reason = f"score {hit.score:.2f} below threshold {MIN_SCORE}"
        elif hit.coverage < MIN_COVERAGE:
            hit.rejection_reason = f"coverage {hit.coverage:.2f} below threshold {MIN_COVERAGE}"
        (rejected if hit.rejection_reason else accepted).append(hit)

    for hit in accepted[:limit]:
        if hit.matched_tokens:
            hit.excerpt = session.execute(
                sql_text(_EXCERPT_SQL),
                {"object_id": hit.object_id, "token": hit.matched_tokens[0]},
            ).scalar()

    return RetrievalResult(
        hits=accepted[:limit],
        rejected=rejected,
        query_tokens=tokens,
        token_weights=weights,
        corpus_objects=corpus_objects,
        threshold={
            "min_score": MIN_SCORE,
            "min_matched": MIN_MATCHED_TOKENS,
            "min_coverage": MIN_COVERAGE,
        },
    )
