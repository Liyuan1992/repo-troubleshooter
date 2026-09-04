"""Evidence packet.

Every claim the product makes must point at an item in here, and every item must
be resolvable afterwards by its id (``get-evidence``). Retrieved text alone is
not a fact: it becomes evidence only once it has an id, a locator, a source
time, and the time it became publicly knowable.

Evidence ids are stable and human-readable on purpose::

    ev:discussion:5084
    ev:comment:5084#DC_kwDO...
    ev:release:dsh-v0.1.2-alpha.2
    ev:commit:675efe73f2d8
    ev:doc:docs/cordis-api/service.md@dsh-v0.1.2-alpha.2
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.diagnosis.contract import EvidenceRef
from repo_troubleshooter.store.models import (
    GitCommit,
    ObjectRevision,
    Release,
    Repository,
    SourceObject,
)

MAX_EXCERPT = 600


@dataclass
class EvidenceItem:
    id: str
    source_type: str  # discussion | discussion_comment | release | commit | doc_file
    locator: str
    role: str  # symptom | change | release | action | context
    url: str | None = None
    title: str | None = None
    excerpt: str | None = None
    # When it happened upstream vs when it first became publicly knowable.
    source_event_time: dt.datetime | None = None
    knowledge_available_time: dt.datetime | None = None
    # When *we* observed it. Kept separate from both of the above (spec section 9).
    observed_at: dt.datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_ref(self) -> EvidenceRef:
        return EvidenceRef(
            id=self.id,
            source_type=self.source_type,
            locator=self.locator,
            url=self.url,
            role=self.role,
            source_event_time=self.source_event_time,
            knowledge_available_time=self.knowledge_available_time,
            excerpt=self.excerpt,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "locator": self.locator,
            "role": self.role,
            "url": self.url,
            "title": self.title,
            "source_event_time": (
                self.source_event_time.isoformat() if self.source_event_time else None
            ),
            "knowledge_available_time": (
                self.knowledge_available_time.isoformat() if self.knowledge_available_time else None
            ),
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "excerpt": self.excerpt,
            "extra": self.extra,
        }


@dataclass
class EvidencePacket:
    """The only thing synthesis is allowed to look at."""

    items: dict[str, EvidenceItem] = field(default_factory=dict)

    def add(self, item: EvidenceItem) -> str:
        existing = self.items.get(item.id)
        if existing is None:
            self.items[item.id] = item
        elif existing.role == "context" and item.role != "context":
            self.items[item.id] = item
        return item.id

    def by_role(self, role: str) -> list[EvidenceItem]:
        return [i for i in self.items.values() if i.role == role]

    def has(self, evidence_id: str) -> bool:
        return evidence_id in self.items

    def refs(self) -> list[EvidenceRef]:
        order = {"symptom": 0, "change": 1, "release": 2, "action": 3, "context": 4}
        return [
            item.to_ref()
            for item in sorted(self.items.values(), key=lambda i: (order.get(i.role, 9), i.id))
        ]


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed[:MAX_EXCERPT] + ("…" if len(collapsed) > MAX_EXCERPT else "")


def discussion_evidence_id(number: int | None, native_id: str) -> str:
    return f"ev:discussion:{number}" if number else f"ev:discussion:{native_id}"


def release_evidence_id(tag: str) -> str:
    return f"ev:release:{tag}"


def commit_evidence_id(sha: str) -> str:
    return f"ev:commit:{sha[:12]}"


def doc_evidence_id(path: str, tag: str) -> str:
    return f"ev:doc:{path}@{tag}"


# --- builders ---------------------------------------------------------------


def from_source_object(
    session: Session, obj: SourceObject, *, role: str, excerpt: str | None = None
) -> EvidenceItem:
    revision = session.scalar(
        select(ObjectRevision)
        .where(ObjectRevision.object_id == obj.id, ObjectRevision.is_current.is_(True))
        .limit(1)
    )
    body = excerpt or (revision.body if revision else None)
    if obj.kind == "discussion":
        evidence_id = discussion_evidence_id(obj.number, obj.native_id)
    elif obj.kind in {"issue", "pull_request"}:
        evidence_id = f"ev:{obj.kind}:{obj.number or obj.native_id}"
    elif obj.kind == "discussion_comment":
        evidence_id = f"ev:comment:{obj.native_id}"
    elif obj.kind == "release":
        evidence_id = release_evidence_id(obj.native_id)
    elif obj.kind == "doc_file":
        evidence_id = f"ev:doc:{obj.native_id}"
    else:
        evidence_id = f"ev:{obj.kind}:{obj.native_id}"

    return EvidenceItem(
        id=evidence_id,
        source_type=obj.kind,
        locator=str(obj.number) if obj.number else obj.native_id,
        role=role,
        url=obj.url,
        title=obj.title,
        excerpt=_excerpt(body),
        source_event_time=obj.source_created_at,
        # For public GitHub content, "created upstream" is when it became knowable.
        knowledge_available_time=obj.source_created_at,
        observed_at=obj.first_observed_at,
        extra={
            "state": obj.state,
            "category": obj.category,
            "author_association": obj.author_association,
            "last_source_update": (
                obj.source_updated_at.isoformat() if obj.source_updated_at else None
            ),
        },
    )


def from_release(release: Release, *, role: str = "release") -> EvidenceItem:
    return EvidenceItem(
        id=release_evidence_id(release.tag_name),
        source_type="release",
        locator=release.tag_name,
        role=role,
        url=release.url,
        title=release.name or release.tag_name,
        excerpt=_excerpt(release.body),
        source_event_time=release.published_at,
        knowledge_available_time=release.published_at,
        observed_at=release.observed_at,
        extra={
            "version_norm": release.version_norm,
            "is_prerelease": release.is_prerelease,
            "commit_sha": release.commit_sha,
        },
    )


def from_commit(
    commit: GitCommit, *, role: str = "change", files: list[str] | None = None
) -> EvidenceItem:
    return EvidenceItem(
        id=commit_evidence_id(commit.sha),
        source_type="commit",
        locator=commit.sha,
        role=role,
        url=None,
        title=commit.subject,
        excerpt=_excerpt(commit.subject),
        source_event_time=commit.authored_at or commit.committed_at,
        knowledge_available_time=commit.committed_at or commit.authored_at,
        observed_at=commit.observed_at,
        extra={"files": files or [], "short_sha": commit.short_sha},
    )


# --- resolution (get-evidence) ----------------------------------------------


def resolve(session: Session, repo: Repository, evidence_id: str) -> EvidenceItem | None:
    """Turn an evidence id back into its source. The inverse of the builders."""
    if not evidence_id.startswith("ev:"):
        return None
    _, _, rest = evidence_id.partition("ev:")
    kind, _, locator = rest.partition(":")

    if kind == "discussion":
        stmt = select(SourceObject).where(
            SourceObject.repo_id == repo.id, SourceObject.kind == "discussion"
        )
        stmt = (
            stmt.where(SourceObject.number == int(locator))
            if locator.isdigit()
            else stmt.where(SourceObject.native_id == locator)
        )
        obj = session.scalar(stmt)
        return from_source_object(session, obj, role="symptom") if obj else None

    if kind == "comment":
        obj = session.scalar(
            select(SourceObject).where(
                SourceObject.repo_id == repo.id,
                SourceObject.kind == "discussion_comment",
                SourceObject.native_id == locator,
            )
        )
        return from_source_object(session, obj, role="symptom") if obj else None

    if kind in {"issue", "pull_request", "issue_comment"}:
        stmt = select(SourceObject).where(
            SourceObject.repo_id == repo.id, SourceObject.kind == kind
        )
        stmt = (
            stmt.where(SourceObject.number == int(locator))
            if locator.isdigit() and kind != "issue_comment"
            else stmt.where(SourceObject.native_id == locator)
        )
        obj = session.scalar(stmt)
        role = "change" if kind == "pull_request" else "symptom"
        return from_source_object(session, obj, role=role) if obj else None

    if kind == "release":
        release = session.scalar(
            select(Release).where(Release.repo_id == repo.id, Release.tag_name == locator)
        )
        return from_release(release) if release else None

    if kind == "commit":
        commit = session.scalar(
            select(GitCommit).where(GitCommit.repo_id == repo.id, GitCommit.sha.startswith(locator))
        )
        return from_commit(commit) if commit else None

    if kind == "doc":
        path, _, tag = locator.partition("@")
        obj = session.scalar(
            select(SourceObject).where(
                SourceObject.repo_id == repo.id,
                SourceObject.kind == "doc_file",
                SourceObject.native_id == path,
            )
        )
        if obj is None:
            return None
        item = from_source_object(session, obj, role="context")
        item.extra["tag"] = tag or None
        return item

    return None
