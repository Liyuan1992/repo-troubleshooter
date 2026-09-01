"""Idempotent writes.

Every sync run must be safe to re-run and safe to interrupt. Object identity is
``(repo, kind, native_id)``; body changes append an ``ObjectRevision`` instead
of overwriting, so an edited discussion keeps its history.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, TypeVar

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from repo_troubleshooter.normalize.content import split_body
from repo_troubleshooter.store.models import (
    ContentUnit,
    GitCommit,
    ObjectRevision,
    RelationAssertion,
    Release,
    ReleaseContainment,
    Repository,
    SourceObject,
    SyncState,
)

_T = TypeVar("_T")

NUL = chr(0)


def _fetch(session: Session, model: type[_T], row_id: int) -> _T:
    """Re-read a row we just wrote. A missing row here is a real bug, not a None."""
    row = session.get(model, row_id)
    if row is None:  # pragma: no cover - would mean the write vanished
        raise RuntimeError(f"{model.__name__} {row_id} disappeared after upsert")
    return row


def sanitize_text(value: str | None) -> str | None:
    """PostgreSQL text cannot hold NUL bytes; upstream bodies sometimes do."""
    if value is None:
        return None
    return value.replace(NUL, "") if NUL in value else value


def body_hash(body: str | None) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --- repository -------------------------------------------------------------


def upsert_repository(
    session: Session,
    *,
    owner: str,
    name: str,
    host: str = "github.com",
    default_branch: str | None = None,
    clone_path: str | None = None,
    profile_name: str | None = None,
    surfaces: dict[str, Any] | None = None,
) -> Repository:
    full_name = f"{owner}/{name}"
    repo = session.scalar(
        select(Repository).where(Repository.host == host, Repository.full_name == full_name)
    )
    if repo is None:
        repo = Repository(host=host, owner=owner, name=name, full_name=full_name, surfaces={})
        session.add(repo)
    if default_branch:
        repo.default_branch = default_branch
    if clone_path:
        repo.clone_path = clone_path
    if profile_name:
        repo.profile_name = profile_name
    if surfaces is not None:
        repo.surfaces = surfaces
    session.flush()
    return repo


def get_repository(session: Session, full_name: str, host: str = "github.com") -> Repository | None:
    return session.scalar(
        select(Repository).where(Repository.host == host, Repository.full_name == full_name)
    )


# --- objects ----------------------------------------------------------------


def upsert_source_object(
    session: Session,
    *,
    repo_id: int,
    kind: str,
    native_id: str,
    **fields: Any,
) -> SourceObject:
    now = utcnow()
    payload: dict[str, Any] = {
        "repo_id": repo_id,
        "kind": kind,
        "native_id": native_id,
        "last_observed_at": now,
        **{k: v for k, v in fields.items() if v is not None},
    }
    stmt = (
        pg_insert(SourceObject)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_source_object_identity",
            set_={k: v for k, v in payload.items() if k not in ("repo_id", "kind", "native_id")},
        )
        .returning(SourceObject.id)
    )
    object_id = session.execute(stmt).scalar_one()
    session.flush()
    return _fetch(session, SourceObject, object_id)


def record_revision(
    session: Session,
    *,
    obj: SourceObject,
    body: str | None,
    source_updated_at: dt.datetime | None,
) -> tuple[ObjectRevision, bool]:
    """Append a revision when the body actually changed. Returns (revision, changed)."""
    body = sanitize_text(body)
    digest = body_hash(body)
    existing = session.scalar(
        select(ObjectRevision).where(
            ObjectRevision.object_id == obj.id, ObjectRevision.body_hash == digest
        )
    )
    if existing is not None:
        if not existing.is_current:
            session.execute(
                update(ObjectRevision)
                .where(ObjectRevision.object_id == obj.id, ObjectRevision.is_current.is_(True))
                .values(is_current=False)
            )
            existing.is_current = True
            session.flush()
        return existing, False

    session.execute(
        update(ObjectRevision)
        .where(ObjectRevision.object_id == obj.id, ObjectRevision.is_current.is_(True))
        .values(is_current=False)
    )
    revision = ObjectRevision(
        object_id=obj.id,
        body=body,
        body_hash=digest,
        source_updated_at=source_updated_at,
        is_current=True,
    )
    session.add(revision)
    session.flush()
    return revision, True


def rebuild_content_units(
    session: Session, *, repo_id: int, obj: SourceObject, revision: ObjectRevision
) -> int:
    """(Re)build content units for one revision. Idempotent per revision."""
    session.query(ContentUnit).filter(ContentUnit.revision_id == revision.id).delete(
        synchronize_session=False
    )
    drafts = split_body(revision.body)
    for draft in drafts:
        session.add(
            ContentUnit(
                repo_id=repo_id,
                object_id=obj.id,
                revision_id=revision.id,
                unit_type=draft.unit_type,
                seq=draft.seq,
                text=draft.text,
                lang=draft.lang,
                extra={},
            )
        )
    session.flush()
    return len(drafts)


# --- releases and commits ---------------------------------------------------


def upsert_release(session: Session, *, repo_id: int, tag_name: str, **fields: Any) -> Release:
    payload = {"repo_id": repo_id, "tag_name": tag_name, "observed_at": utcnow(), **fields}
    stmt = (
        pg_insert(Release)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_release_tag",
            set_={k: v for k, v in payload.items() if k not in ("repo_id", "tag_name")},
        )
        .returning(Release.id)
    )
    release_id = session.execute(stmt).scalar_one()
    session.flush()
    return _fetch(session, Release, release_id)


def upsert_commit(session: Session, *, repo_id: int, sha: str, **fields: Any) -> GitCommit:
    payload = {"repo_id": repo_id, "sha": sha, **fields}
    stmt = (
        pg_insert(GitCommit)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_git_commit_sha",
            set_={k: v for k, v in payload.items() if k not in ("repo_id", "sha")},
        )
        .returning(GitCommit.id)
    )
    commit_id = session.execute(stmt).scalar_one()
    session.flush()
    return _fetch(session, GitCommit, commit_id)


def upsert_containment(
    session: Session,
    *,
    repo_id: int,
    release_id: int,
    commit_sha: str,
    contains: bool,
    evidence: dict[str, Any],
) -> ReleaseContainment:
    payload = {
        "repo_id": repo_id,
        "release_id": release_id,
        "commit_sha": commit_sha,
        "contains": contains,
        "evidence": evidence,
        "computed_at": utcnow(),
    }
    stmt = (
        pg_insert(ReleaseContainment)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_release_containment",
            set_={
                "contains": contains,
                "evidence": evidence,
                "computed_at": payload["computed_at"],
            },
        )
        .returning(ReleaseContainment.id)
    )
    row_id = session.execute(stmt).scalar_one()
    session.flush()
    return _fetch(session, ReleaseContainment, row_id)


# --- relations --------------------------------------------------------------


def assert_relation(
    session: Session,
    *,
    repo_id: int,
    relation_type: str,
    src_object_id: int,
    dst_object_id: int | None = None,
    dst_ref: str | None = None,
    derivation: str,
    confidence: str = "high",
    evidence: dict[str, Any] | None = None,
) -> None:
    """Record an edge. `derivation` is mandatory: an inference never becomes a fact."""
    payload = {
        "repo_id": repo_id,
        "relation_type": relation_type,
        "src_object_id": src_object_id,
        "dst_object_id": dst_object_id,
        "dst_ref": dst_ref,
        "derivation": derivation,
        "confidence": confidence,
        "evidence": evidence or {},
    }
    stmt = (
        pg_insert(RelationAssertion)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_relation_assertion",
            set_={"confidence": confidence, "evidence": payload["evidence"]},
        )
    )
    session.execute(stmt)


# --- sync state -------------------------------------------------------------


def get_sync_state(session: Session, repo_id: int, source: str) -> SyncState:
    state = session.scalar(
        select(SyncState).where(SyncState.repo_id == repo_id, SyncState.source == source)
    )
    if state is None:
        state = SyncState(repo_id=repo_id, source=source, status="unknown", stats={})
        session.add(state)
        session.flush()
    return state


def mark_sync_start(session: Session, repo_id: int, source: str) -> SyncState:
    state = get_sync_state(session, repo_id, source)
    state.last_attempt_at = utcnow()
    state.status = "running"
    session.flush()
    return state


def mark_sync_success(
    session: Session,
    repo_id: int,
    source: str,
    *,
    watermark: dt.datetime | None = None,
    objects_seen: int = 0,
    full: bool = False,
    status: str = "complete",
    stats: dict[str, Any] | None = None,
) -> SyncState:
    state = get_sync_state(session, repo_id, source)
    now = utcnow()
    state.last_success_at = now
    if full:
        state.last_full_sync_at = now
    if watermark and (state.watermark is None or watermark > state.watermark):
        state.watermark = watermark
    state.objects_seen = (state.objects_seen or 0) + objects_seen
    state.status = status
    state.consecutive_failures = 0
    state.last_error = None
    if stats:
        merged = dict(state.stats or {})
        merged.update(stats)
        state.stats = merged
    session.flush()
    return state


def mark_sync_failure(session: Session, repo_id: int, source: str, error: str) -> SyncState:
    state = get_sync_state(session, repo_id, source)
    state.status = "failed"
    state.consecutive_failures = (state.consecutive_failures or 0) + 1
    state.last_error = error[:4000]
    session.flush()
    return state
