"""SQLAlchemy models for the V0 data spine.

Design rules that come straight from the spec and must not be softened:

* Raw facts and derived facts live in different places. Anything GitHub or git
  states directly is a row on the object tables; anything we concluded is a
  ``RelationAssertion`` carrying its ``derivation`` and evidence pointer.
* ``ReleaseContainment`` records only that a commit is an ancestor of a tag.
  It never means "the bug is fixed in that release".
* Source time, observation time and applicability are separate columns, never
  a single ``created_at``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow_col() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


OBJECT_KINDS = (
    "discussion",
    "discussion_comment",
    "issue",
    "issue_comment",
    "pull_request",
    "release",
    "commit",
    "doc_file",
)

# Only relations we can justify. `derivation` says how we know.
RELATION_TYPES = (
    "REFERENCES",
    "DUPLICATE_OF",
    "CLOSES",
    "RESOLVES",
    "ANSWERED_BY",
    "PR_MERGED_AS",
    "RELEASE_CONTAINS_COMMIT",
    "DOC_SNAPSHOT_AT_COMMIT",
    "REVERTS",
    "BACKPORT_OF",
)

DERIVATIONS = ("github_native", "git_deterministic", "text_explicit", "inferred")

_KINDS_SQL = ", ".join(f"'{k}'" for k in OBJECT_KINDS)


class Repository(Base):
    __tablename__ = "repository"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(64), default="github.com", nullable=False)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    full_name: Mapped[str] = mapped_column(String(300), nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String(200))
    clone_path: Mapped[str | None] = mapped_column(Text)
    profile_name: Mapped[str | None] = mapped_column(String(128))
    # Result of the live surface probe: which sources actually exist here.
    surfaces: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at = _utcnow_col()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("host", "full_name", name="uq_repository_host_full_name"),)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Repository {self.full_name}>"


class SourceObject(Base):
    """One addressable upstream artifact (discussion, comment, release, ...)."""

    __tablename__ = "source_object"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stable upstream identity: GraphQL node id, sha, tag name, or docs path.
    native_id: Mapped[str] = mapped_column(String(400), nullable=False)
    number: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(64))
    author: Mapped[str | None] = mapped_column(String(200))
    author_association: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(128))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_object.id", ondelete="CASCADE")
    )

    # --- time, kept deliberately separate (spec section 9) ---
    source_created_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    source_closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    first_observed_at = _utcnow_col()
    last_observed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    revisions: Mapped[list[ObjectRevision]] = relationship(
        back_populates="obj", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("repo_id", "kind", "native_id", name="uq_source_object_identity"),
        CheckConstraint(f"kind IN ({_KINDS_SQL})", name="ck_source_object_kind"),
        Index("ix_source_object_repo_kind", "repo_id", "kind"),
        Index("ix_source_object_updated", "repo_id", "kind", "source_updated_at"),
        Index("ix_source_object_number", "repo_id", "kind", "number"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<SourceObject {self.kind} #{self.number or self.native_id}>"


class ObjectRevision(Base):
    """A body snapshot. Upstream edits create a new revision, never an overwrite."""

    __tablename__ = "object_revision"

    id: Mapped[int] = mapped_column(primary_key=True)
    object_id: Mapped[int] = mapped_column(
        ForeignKey("source_object.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str | None] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at = _utcnow_col()
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    obj: Mapped[SourceObject] = relationship(back_populates="revisions")

    __table_args__ = (
        UniqueConstraint("object_id", "body_hash", name="uq_object_revision_body"),
        Index("ix_object_revision_current", "object_id", "is_current"),
    )


class ContentUnit(Base):
    """Retrievable text unit derived from a revision (paragraph, code block, log block)."""

    __tablename__ = "content_unit"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    object_id: Mapped[int] = mapped_column(
        ForeignKey("source_object.id", ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[int] = mapped_column(
        ForeignKey("object_revision.id", ondelete="CASCADE"), nullable=False
    )
    unit_type: Mapped[str] = mapped_column(String(32), nullable=False)  # prose|code|log|config
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str | None] = mapped_column(String(16))
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("revision_id", "seq", name="uq_content_unit_seq"),
        Index("ix_content_unit_object", "object_id"),
        # Retrieval scans this column with ILIKE '%token%'.
        Index(
            "ix_content_unit_text_trgm",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
    )


class GitCommit(Base):
    """Materialised only for commits we actually reference (spec forbids full commit ingest)."""

    __tablename__ = "git_commit"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    sha: Mapped[str] = mapped_column(String(40), nullable=False)
    short_sha: Mapped[str] = mapped_column(String(12), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    author_name: Mapped[str | None] = mapped_column(String(200))
    authored_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    parents: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    observed_at = _utcnow_col()

    __table_args__ = (
        UniqueConstraint("repo_id", "sha", name="uq_git_commit_sha"),
        Index("ix_git_commit_short", "repo_id", "short_sha"),
    )


class Release(Base):
    __tablename__ = "release"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    tag_name: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    # Normalised comparable version, e.g. dsh-v0.1.2-alpha.3 -> 0.1.2-alpha.3
    version_norm: Mapped[str | None] = mapped_column(String(120))
    is_prerelease: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    # 'github_release' | 'git_tag'  -- a tag with no release row is still usable.
    origin: Mapped[str] = mapped_column(String(32), default="github_release", nullable=False)
    observed_at = _utcnow_col()

    __table_args__ = (
        UniqueConstraint("repo_id", "tag_name", name="uq_release_tag"),
        Index("ix_release_published", "repo_id", "published_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Release {self.tag_name}>"


class ReleaseContainment(Base):
    """Cache of ``git tag --contains``.

    Proves exactly one thing: the commit is an ancestor of the tag. It is NOT
    evidence that any runtime symptom was resolved in that release.
    """

    __tablename__ = "release_containment"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    release_id: Mapped[int] = mapped_column(
        ForeignKey("release.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    contains: Mapped[bool] = mapped_column(Boolean, nullable=False)
    computed_at = _utcnow_col()
    # git command transcript kept so the claim can be re-verified.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("release_id", "commit_sha", name="uq_release_containment"),
        Index("ix_release_containment_commit", "repo_id", "commit_sha"),
    )


class RelationAssertion(Base):
    """Every edge, raw or derived, with how we came to believe it."""

    __tablename__ = "relation_assertion"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(48), nullable=False)
    src_object_id: Mapped[int] = mapped_column(
        ForeignKey("source_object.id", ondelete="CASCADE"), nullable=False
    )
    dst_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_object.id", ondelete="CASCADE")
    )
    # For edges pointing at things that are not source objects (a raw sha, a tag).
    dst_ref: Mapped[str | None] = mapped_column(String(400))
    derivation: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), default="high", nullable=False)
    # Where the claim is visible: revision id, or a git command transcript.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    observed_at = _utcnow_col()

    __table_args__ = (
        # PostgreSQL treats NULLs as distinct by default, so a re-sync used to
        # insert a second copy of every edge whose dst_object_id was NULL.
        UniqueConstraint(
            "repo_id",
            "relation_type",
            "src_object_id",
            "dst_object_id",
            "dst_ref",
            name="uq_relation_assertion",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "derivation IN ('github_native','git_deterministic','text_explicit','inferred')",
            name="ck_relation_derivation",
        ),
        CheckConstraint("confidence IN ('high','medium','low')", name="ck_relation_confidence"),
        Index("ix_relation_src", "repo_id", "src_object_id", "relation_type"),
        Index("ix_relation_dst", "repo_id", "dst_object_id", "relation_type"),
    )


class SyncState(Base):
    """Per (repo, source) sync bookkeeping: cursor, health, coverage."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # discussions|releases|git|docs
    cursor: Mapped[str | None] = mapped_column(Text)
    # High-water mark on the upstream clock, used for incremental resume.
    watermark: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_sync_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    objects_seen: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("repo_id", "source", name="uq_sync_state"),
        CheckConstraint(
            "status IN ('unknown','complete','degraded','stale','failed','running')",
            name="ck_sync_status",
        ),
    )


class IncidentResolutionRecord(Base):
    """A derived, reviewable record of "this symptom, this change, this release".

    Deliberately not called a FixRecord. Every field says how it was obtained,
    and ``runtime_verified`` stays false unless a human confirms a reproduction:
    containment plus a release note is not proof that a user's symptom is gone.
    """

    __tablename__ = "incident_resolution_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    # Stable key for the symptom, from the error fingerprint.
    incident_key: Mapped[str] = mapped_column(String(64), nullable=False)
    symptom_signature: Mapped[str | None] = mapped_column(Text)
    symptom_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_object.id", ondelete="SET NULL")
    )

    # Evidence, by id, so the record can be audited without re-running retrieval.
    symptom_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    change_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    release_evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    candidate_fix_commit: Mapped[str | None] = mapped_column(String(40))
    first_release_containing_change: Mapped[str | None] = mapped_column(String(200))
    release_set: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    affected_constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    reported_versions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    maintainer_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    release_contains_change: Mapped[bool | None] = mapped_column(Boolean)
    # Only a human reproduction can set this. Containment never does.
    runtime_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    workaround: Mapped[str | None] = mapped_column(Text)

    evidence_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    derivation: Mapped[str] = mapped_column(String(32), default="inferred", nullable=False)
    review_state: Mapped[str] = mapped_column(String(16), default="derived", nullable=False)
    conflicts: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at = _utcnow_col()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("repo_id", "incident_key", name="uq_incident_key"),
        CheckConstraint(
            "review_state IN ('derived','reviewed','rejected')", name="ck_incident_review_state"
        ),
        CheckConstraint(
            "evidence_level IN ('high','medium','low')", name="ck_incident_evidence_level"
        ),
        Index("ix_incident_repo", "repo_id", "incident_key"),
    )


class SymptomSignature(Base):
    """A feature mined from one source object's own text.

    Signatures exist so a paraphrase can still find the incident: a reporter who
    writes "the boot graph has no entries" and one who pastes the exact global
    name share behavioural features even when they share no tokens.

    Every row is derived from real upstream text - there is no hand-written
    alias list - and ``derivation`` records that.
    """

    __tablename__ = "symptom_signature"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), nullable=False
    )
    object_id: Mapped[int] = mapped_column(
        ForeignKey("source_object.id", ondelete="CASCADE"), nullable=False
    )
    # subject_strong | subject_weak | error | structural | behavior | component | cause
    feature_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_value: Mapped[str] = mapped_column(String(300), nullable=False)
    # Which source text produced it: the thread body, a comment, a release note.
    derivation: Mapped[str] = mapped_column(String(32), default="mined", nullable=False)
    observed_at = _utcnow_col()

    __table_args__ = (
        UniqueConstraint("object_id", "feature_kind", "feature_value", name="uq_symptom_signature"),
        Index("ix_symptom_signature_lookup", "repo_id", "feature_kind", "feature_value"),
        Index("ix_symptom_signature_object", "object_id"),
        CheckConstraint(
            "feature_kind IN ('subject_strong','subject_weak','error','structural',"
            "'behavior','component','cause')",
            name="ck_symptom_signature_kind",
        ),
    )
