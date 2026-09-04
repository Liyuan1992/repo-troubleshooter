"""Mining symptom signatures from upstream text.

For every incident-bearing object we store the feature classes found in *its own*
sources - the thread body and its comments. Nothing here is hand-written: an
alias exists only because some real reporter wrote it, which is what makes a
later paraphrase match defensible rather than invented.

Signatures are additive and idempotent: re-running after a sync inserts only
what is new.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from repo_troubleshooter.fingerprint import features as feat
from repo_troubleshooter.fingerprint.features import SymptomFeatures
from repo_troubleshooter.fingerprint.subjects import (
    FEATURE_EXTRACTOR_VERSION,
    module_names_from_subjects,
)
from repo_troubleshooter.store.models import (
    ContentUnit,
    Repository,
    SourceObject,
    SymptomSignature,
    SyncState,
)

# Objects that can carry a symptom. Docs describe intent, not failures.
SIGNATURE_KINDS = ("discussion", "issue", "release")
MAX_TEXT_CHARS = 20000


@dataclass
class SignatureStats:
    """Three different numbers that were previously conflated into one.

    Mining runs twice - once on what each thread proves alone, once more after
    the corpus can vouch for module names - and most rows in the second pass
    already exist. Adding both passes together reported roughly twice the rows
    the database actually holds, so the counts are kept apart:

    ``rows_attempted`` how many (kind, value) pairs were offered;
    ``rows_inserted`` how many of those were new (conflicts excluded);
    ``rows_stored_total`` how many rows the repository actually has afterwards.
    """

    objects: int = 0
    rows_attempted: int = 0
    rows_inserted: int = 0
    rows_stored_total: int = 0
    skipped_empty: int = 0
    passes: int = 1
    extractor_version: int = FEATURE_EXTRACTOR_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "objects": self.objects,
            "rows_attempted": self.rows_attempted,
            "rows_inserted": self.rows_inserted,
            "rows_stored_total": self.rows_stored_total,
            "skipped_empty": self.skipped_empty,
            "passes": self.passes,
            "extractor_version": self.extractor_version,
        }


def object_text(session: Session, object_id: int, *, include_children: bool = True) -> str:
    """The object's own text plus its comments - one incident, one vocabulary."""
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
    return "\n".join(rows)[:MAX_TEXT_CHARS]


def features_for_object(
    session: Session, obj: SourceObject, known_modules: frozenset[str] = frozenset()
) -> SymptomFeatures:
    title = obj.title or ""
    return feat.extract(f"{title}\n{object_text(session, obj.id)}", known_modules=known_modules)


def known_modules(session: Session, repo_id: int) -> frozenset[str]:
    """Module names this repository's own strong subjects vouch for."""
    rows = session.scalars(
        select(SymptomSignature.feature_value).where(
            SymptomSignature.repo_id == repo_id,
            SymptomSignature.feature_kind.in_(("subject_package", "subject_path")),
        )
    ).all()
    return frozenset(module_names_from_subjects(set(rows)))


def store_features(
    session: Session, *, repo_id: int, object_id: int, features: SymptomFeatures
) -> tuple[int, int]:
    """Insert the object's features. Returns (attempted, actually inserted)."""
    rows = features.as_rows()
    if not rows:
        return 0, 0
    quoted_only = features.quoted_only
    stmt = (
        pg_insert(SymptomSignature)
        .values(
            [
                {
                    "repo_id": repo_id,
                    "object_id": object_id,
                    "feature_kind": kind,
                    "feature_value": value[:300],
                    "derivation": "mined",
                    "quoted": value in quoted_only,
                }
                for kind, value in rows
            ]
        )
        .on_conflict_do_nothing(constraint="uq_symptom_signature")
        .returning(SymptomSignature.id)
    )
    inserted = len(session.execute(stmt).scalars().all())
    return len(rows), inserted


def build_for_repository(
    session: Session,
    repo: Repository,
    *,
    kinds: tuple[str, ...] = SIGNATURE_KINDS,
    rebuild: bool = False,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> SignatureStats:
    """Mine signatures for every incident-bearing object of one repository."""
    stats = SignatureStats()

    if rebuild:
        session.execute(delete(SymptomSignature).where(SymptomSignature.repo_id == repo.id))
        session.flush()

    query = select(SourceObject).where(
        SourceObject.repo_id == repo.id, SourceObject.kind.in_(kinds)
    )
    if limit:
        query = query.limit(limit)

    # Pass 1: mine what each thread proves on its own.
    for obj in session.scalars(query):
        features = features_for_object(session, obj)
        if features.is_empty():
            stats.skipped_empty += 1
            continue
        attempted, inserted = store_features(
            session, repo_id=repo.id, object_id=obj.id, features=features
        )
        stats.rows_attempted += attempted
        stats.rows_inserted += inserted
        stats.objects += 1
        if progress and stats.objects % 100 == 0:
            progress(f"signatures: {stats.objects} objects mined")
        if stats.objects % 200 == 0:
            session.commit()
    session.commit()

    # Pass 2: now that the corpus knows which module names are real, re-mine so
    # a thread that mentions `web-search-deepseek` in prose gets it as a subject.
    corpus = known_modules(session, repo.id)
    if corpus:
        rescanned = 0
        for obj in session.scalars(query):
            features = features_for_object(session, obj, corpus)
            if features.is_empty():
                continue
            attempted, inserted = store_features(
                session, repo_id=repo.id, object_id=obj.id, features=features
            )
            stats.rows_attempted += attempted
            stats.rows_inserted += inserted
            rescanned += 1
            if rescanned % 200 == 0:
                session.commit()
        session.commit()
        stats.passes = 2
        if progress:
            progress(f"signatures: corpus pass over {rescanned} objects")

    stats.rows_stored_total = (
        session.scalar(
            select(func.count())
            .select_from(SymptomSignature)
            .where(SymptomSignature.repo_id == repo.id)
        )
        or 0
    )
    _record_extractor_version(session, repo, stats)
    session.commit()
    return stats


def _record_extractor_version(
    session: Session, repo: Repository, stats: SignatureStats | None = None
) -> None:
    """Stamp the mined rows with the extractor that produced them."""
    state = session.scalar(
        select(SyncState).where(SyncState.repo_id == repo.id, SyncState.source == "signatures")
    )
    if state is None:
        state = SyncState(repo_id=repo.id, source="signatures", status="complete", stats={})
        session.add(state)
        session.flush()
    recorded = dict(state.stats or {})
    recorded["extractor_version"] = FEATURE_EXTRACTOR_VERSION
    if stats is not None:
        recorded.update(stats.to_json())
        # An invalidation marks this source stale and zeroes its counts, so
        # a finished build has to clear that mark. Otherwise the reading only
        # flips from one false state to the other: rows present, status stale.
        state.status = "complete"
        state.objects_seen = stats.rows_stored_total
        state.last_success_at = dt.datetime.now(dt.UTC)
    state.stats = recorded
    session.flush()


_FEATURE_FIELD = {
    "subject_package": "subject_packages",
    "subject_path": "subject_paths",
    "subject_dependency": "subject_dependencies",
    "subject_confirmed_non_primary": "subject_confirmed_non_primary",
    "subject_conflicted": "subject_conflicted",
    "subject_unresolved": "subject_unresolved",
    "subject_builtin": "subject_builtins",
    "subject_module": "subject_modules",
    "error": "error",
    "structural": "structural",
    "behavior": "behavior",
    "component": "component",
    "cause": "causes",
}


def _collect(rows: list[tuple[str, str, bool]], *, skip_quoted: bool) -> SymptomFeatures:
    features = SymptomFeatures()
    for kind, value, quoted in rows:
        if skip_quoted and quoted:
            continue
        field_name = _FEATURE_FIELD.get(kind)
        if field_name is not None:
            getattr(features, field_name).add(value)
    return features


def load_features(session: Session, object_id: int) -> SymptomFeatures:
    """Read back one object's stored features, quoted provenance included.

    A candidate that is itself largely a quotation - an upstream thread whose
    evidence sits inside a fence it pasted - must not identify a query on the
    strength of what it quoted, the same way a query must not. That means the
    flag has to survive storage, not just live in the process that mined it.
    """
    rows = [
        (kind, value, bool(quoted))
        for kind, value, quoted in session.execute(
            select(
                SymptomSignature.feature_kind,
                SymptomSignature.feature_value,
                SymptomSignature.quoted,
            ).where(SymptomSignature.object_id == object_id)
        ).all()
    ]
    features = _collect(rows, skip_quoted=False)
    if any(quoted for _, _, quoted in rows):
        unquoted = _collect(rows, skip_quoted=True)
        features.quoted_only = feat.identity_values(features) - feat.identity_values(unquoted)
        features.unquoted = unquoted
    return features


def document_frequencies(session: Session, repo_id: int, values: set[str]) -> dict[str, int]:
    """How many objects carry each feature value - rarity is what makes it identify."""
    if not values:
        return {}
    rows = session.execute(
        select(SymptomSignature.feature_value, SymptomSignature.object_id)
        .where(
            SymptomSignature.repo_id == repo_id,
            SymptomSignature.feature_value.in_(list(values)),
        )
        .distinct()
    ).all()
    counts: dict[str, int] = {}
    for value, _object_id in rows:
        counts[value] = counts.get(value, 0) + 1
    return counts


class SignaturesStale(RuntimeError):
    """The mined signatures cannot be trusted for diagnosis."""


@dataclass
class SignatureState:
    """Whether this repository's mined signatures match the current extractor."""

    rows: int
    stored_version: int | None
    current_version: int = FEATURE_EXTRACTOR_VERSION

    @property
    def ok(self) -> bool:
        return self.rows > 0 and self.stored_version == self.current_version

    def remediation(self, repo_full_name: str) -> str:
        if self.rows == 0:
            return (
                "no symptom signatures are stored for "
                f"{repo_full_name}; diagnosis would be blind to paraphrases.\n"
                f"  build them with:  repo-troubleshooter signatures {repo_full_name}"
            )
        return (
            f"symptom signatures for {repo_full_name} were mined by extractor "
            f"version {self.stored_version}, but this build is version "
            f"{self.current_version}; stored features no longer mean the same "
            "thing as query features.\n"
            f"  rebuild them with:  repo-troubleshooter signatures {repo_full_name} --rebuild"
        )


def signature_state(session: Session, repo: Repository) -> SignatureState:
    rows = (
        session.scalar(
            select(func.count())
            .select_from(SymptomSignature)
            .where(SymptomSignature.repo_id == repo.id)
        )
        or 0
    )
    state = session.scalar(
        select(SyncState).where(SyncState.repo_id == repo.id, SyncState.source == "signatures")
    )
    stored = (state.stats or {}).get("extractor_version") if state else None
    return SignatureState(
        rows=rows, stored_version=int(stored) if isinstance(stored, int) else None
    )


def require_fresh_signatures(session: Session, repo: Repository) -> SignatureState:
    """Raise unless the stored signatures were mined by this exact extractor."""
    state = signature_state(session, repo)
    if not state.ok:
        raise SignaturesStale(state.remediation(repo.full_name))
    return state
