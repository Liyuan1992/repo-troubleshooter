"""Sync orchestration.

One entry point per repository. Every source is synced independently so a
failure in one (say GraphQL rate limiting on discussions) degrades that source
only, and the result is always reported honestly through ``sync_state``:
a partial world is never presented as a complete one.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import cast, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from repo_troubleshooter.config import Settings, get_settings
from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.connectors.github.client import GitHubClient
from repo_troubleshooter.connectors.github.discussions import Discussion, iter_discussions
from repo_troubleshooter.connectors.github.probe import RepoSurfaces, probe_repository
from repo_troubleshooter.connectors.github.releases import iter_releases
from repo_troubleshooter.profiles.loader import RepoProfile
from repo_troubleshooter.relations.extract import extract_references
from repo_troubleshooter.relations.signatures import build_for_repository
from repo_troubleshooter.store.db import session_scope
from repo_troubleshooter.store.models import Release, Repository, SourceObject
from repo_troubleshooter.sync import upsert
from repo_troubleshooter.versions import semver

ProgressFn = Callable[[str], None]

# Docs trees carry images and fixtures. Only text is evidence.
TEXT_DOC_SUFFIXES = {
    ".md",
    ".mdx",
    ".markdown",
    ".rst",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".jsonc",
    ".toml",
    ".ini",
    ".cfg",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".py",
    ".sh",
    ".sql",
    ".env",
}


def _noop(_message: str) -> None:
    return None


@dataclass
class SourceReport:
    source: str
    status: str = "skipped"
    objects: int = 0
    changed: int = 0
    duration_s: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class SyncReport:
    repo: str
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    surfaces: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, SourceReport] = field(default_factory=dict)

    @property
    def health(self) -> str:
        """complete only when every attempted source completed."""
        statuses = {r.status for r in self.sources.values() if r.status != "skipped"}
        if not statuses:
            return "stale"
        if statuses == {"complete"}:
            return "complete"
        if "failed" in statuses and statuses == {"failed"}:
            return "failed"
        return "degraded"

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "health": self.health,
            "surfaces": self.surfaces,
            "sources": {
                name: {
                    "status": r.status,
                    "objects": r.objects,
                    "changed": r.changed,
                    "duration_s": round(r.duration_s, 2),
                    "detail": r.detail,
                    "error": r.error,
                }
                for name, r in self.sources.items()
            },
        }


# --- helpers ----------------------------------------------------------------


def normalize_tag(tag: str, profile: RepoProfile) -> str | None:
    candidate = tag
    for prefix in profile.version.tag_prefixes:
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    return semver.normalize_version(candidate) or semver.normalize_version(tag)


def _tag_ignored(tag: str, profile: RepoProfile) -> bool:
    return any(re.search(pattern, tag) for pattern in profile.version.ignore_tag_patterns)


def _doc_excluded(path: str, profile: RepoProfile) -> bool:
    if Path(path).suffix.lower() not in TEXT_DOC_SUFFIXES:
        return True  # images, fonts and binary fixtures are not evidence
    return any(re.search(pattern, path) for pattern in profile.docs.exclude_patterns)


def _require_repository(session: Session, repo_id: int) -> Repository:
    """The repository row we just wrote must still be there."""
    repo = session.get(Repository, repo_id)
    if repo is None:  # pragma: no cover - would mean the row vanished mid-sync
        raise RuntimeError(f"repository {repo_id} disappeared during sync")
    return repo


def clone_path_for(profile: RepoProfile, settings: Settings) -> Path:
    return Path(settings.clone_root) / f"{profile.slug}.git"


def _record_references(
    session: Session,
    *,
    repo: Repository,
    src_object_id: int,
    text: str | None,
    revision_id: int | None,
) -> int:
    """Persist explicit references found in a body as text_explicit relations."""
    count = 0
    for ref in extract_references(text, self_repo=repo.full_name):
        if ref.kind == "version":
            continue  # version mentions are applicability signals, not edges
        if ref.owner and ref.repo and f"{ref.owner}/{ref.repo}".lower() != repo.full_name.lower():
            continue  # cross-repo references are out of scope for V1
        upsert.assert_relation(
            session,
            repo_id=repo.id,
            relation_type="REFERENCES",
            src_object_id=src_object_id,
            dst_ref=f"{ref.kind}:{ref.value}",
            derivation="text_explicit",
            confidence=ref.confidence,
            evidence={"raw": ref.raw, "revision_id": revision_id},
        )
        count += 1
    return count


def _ingest_body(
    session: Session,
    *,
    repo: Repository,
    obj: SourceObject,
    body: str | None,
    source_updated_at: dt.datetime | None,
) -> bool:
    """Record a revision + content units + references. Returns True when changed."""
    revision, changed = upsert.record_revision(
        session, obj=obj, body=body, source_updated_at=source_updated_at
    )
    if changed:
        upsert.rebuild_content_units(session, repo_id=repo.id, obj=obj, revision=revision)
        _record_references(
            session, repo=repo, src_object_id=obj.id, text=body, revision_id=revision.id
        )
    return changed


# --- per-source sync --------------------------------------------------------


def sync_releases(
    session: Session,
    repo: Repository,
    profile: RepoProfile,
    client: GitHubClient | None,
    git: GitRepo | None,
    progress: ProgressFn = _noop,
) -> SourceReport:
    """GitHub releases first, then any git tag that has no release row."""
    report = SourceReport(source="releases")
    started = time.monotonic()
    upsert.mark_sync_start(session, repo.id, "releases")
    seen_tags: set[str] = set()
    changed = 0

    try:
        if client is not None:
            for record in iter_releases(client, profile.owner, profile.name):
                if _tag_ignored(record.tag_name, profile):
                    continue
                commit_sha = record.commit_sha
                if not commit_sha and git is not None:
                    commit_sha = git.resolve_ref(record.tag_name)
                release = upsert.upsert_release(
                    session,
                    repo_id=repo.id,
                    tag_name=record.tag_name,
                    name=record.name,
                    version_norm=normalize_tag(record.tag_name, profile),
                    is_prerelease=record.is_prerelease,
                    is_draft=record.is_draft,
                    commit_sha=commit_sha,
                    published_at=record.published_at,
                    url=record.url,
                    body=record.body,
                    origin="github_release",
                )
                seen_tags.add(release.tag_name)

                # The release note is itself evidence: index it like any body.
                obj = upsert.upsert_source_object(
                    session,
                    repo_id=repo.id,
                    kind="release",
                    native_id=record.tag_name,
                    number=None,
                    url=record.url,
                    title=record.name or record.tag_name,
                    state="prerelease" if record.is_prerelease else "released",
                    source_created_at=record.published_at,
                    source_updated_at=record.published_at,
                    extra={"tag": record.tag_name, "commit_sha": commit_sha},
                )
                if _ingest_body(
                    session,
                    repo=repo,
                    obj=obj,
                    body=record.body,
                    source_updated_at=record.published_at,
                ):
                    changed += 1
                if commit_sha:
                    upsert.assert_relation(
                        session,
                        repo_id=repo.id,
                        relation_type="RELEASE_CONTAINS_COMMIT",
                        src_object_id=obj.id,
                        dst_ref=f"commit:{commit_sha}",
                        derivation="github_native",
                        evidence={"source": "graphql tagCommit.oid", "tag": record.tag_name},
                    )
                report.objects += 1

        # Tags without a GitHub release still bound versions.
        if git is not None:
            for tag in git.list_tags():
                if tag.name in seen_tags or _tag_ignored(tag.name, profile):
                    continue
                upsert.upsert_release(
                    session,
                    repo_id=repo.id,
                    tag_name=tag.name,
                    version_norm=normalize_tag(tag.name, profile),
                    is_prerelease=bool(semver.is_prerelease(normalize_tag(tag.name, profile))),
                    commit_sha=tag.commit_sha,
                    published_at=tag.tagged_at,
                    origin="git_tag",
                )
                report.objects += 1

        report.changed = changed
        report.status = "complete"
        upsert.mark_sync_success(
            session,
            repo.id,
            "releases",
            objects_seen=report.objects,
            full=True,
            stats={"tags": report.objects},
        )
        progress(f"releases: {report.objects} tracked ({changed} bodies changed)")
    except Exception as exc:  # noqa: BLE001 - recorded, then surfaced in the report
        report.status = "failed"
        report.error = str(exc)
        session.rollback()
        upsert.mark_sync_failure(session, repo.id, "releases", str(exc))
        session.commit()
        progress(f"releases: FAILED {exc}")

    report.duration_s = time.monotonic() - started
    return report


def sync_docs(
    session: Session,
    repo: Repository,
    profile: RepoProfile,
    git: GitRepo,
    *,
    max_releases: int = 8,
    progress: ProgressFn = _noop,
) -> SourceReport:
    """Snapshot versioned docs at each release tag.

    Docs are the best evidence for "how does the API work in MY version", so
    they are stored per tag. Blob shas let us skip files that did not change
    between tags.
    """
    report = SourceReport(source="docs")
    started = time.monotonic()
    upsert.mark_sync_start(session, repo.id, "docs")

    try:
        releases = list(
            session.scalars(
                select(Release).where(Release.repo_id == repo.id, Release.is_draft.is_(False))
            )
        )
        releases.sort(key=lambda r: semver.sort_key(r.version_norm or r.tag_name))
        releases = releases[-max_releases:]
        if not releases:
            report.status = "skipped"
            report.duration_s = time.monotonic() - started
            return report

        seen_blobs: dict[tuple[str, str], int] = {}
        snapshots = 0
        for release in releases:
            for prefix in profile.docs.paths or ["docs/"]:
                for blob_sha, path in git.ls_tree_entries(release.tag_name, prefix):
                    if _doc_excluded(path, profile):
                        continue
                    obj = upsert.upsert_source_object(
                        session,
                        repo_id=repo.id,
                        kind="doc_file",
                        native_id=path,
                        title=path,
                        url=f"{repo.full_name}/blob/{release.tag_name}/{path}",
                        extra={"path": path},
                    )
                    key = (path, blob_sha)
                    if key not in seen_blobs:
                        body = git.show_blob(blob_sha)
                        revision, changed = upsert.record_revision(
                            session,
                            obj=obj,
                            body=body,
                            source_updated_at=release.published_at,
                        )
                        if changed:
                            upsert.rebuild_content_units(
                                session, repo_id=repo.id, obj=obj, revision=revision
                            )
                            report.changed += 1
                        seen_blobs[key] = revision.id
                    upsert.assert_relation(
                        session,
                        repo_id=repo.id,
                        relation_type="DOC_SNAPSHOT_AT_COMMIT",
                        src_object_id=obj.id,
                        dst_ref=f"tag:{release.tag_name}",
                        derivation="git_deterministic",
                        evidence={
                            "blob_sha": blob_sha,
                            "revision_id": seen_blobs[key],
                            "commit_sha": release.commit_sha,
                        },
                    )
                    snapshots += 1
            session.flush()
            progress(f"docs: snapshotted {release.tag_name}")

        report.objects = snapshots
        report.detail = {
            "releases": [r.tag_name for r in releases],
            "distinct_blobs": len(seen_blobs),
        }
        report.status = "complete"
        upsert.mark_sync_success(
            session, repo.id, "docs", objects_seen=snapshots, full=True, stats=report.detail
        )
    except Exception as exc:  # noqa: BLE001
        report.status = "failed"
        report.error = str(exc)
        session.rollback()
        upsert.mark_sync_failure(session, repo.id, "docs", str(exc))
        session.commit()
        progress(f"docs: FAILED {exc}")

    report.duration_s = time.monotonic() - started
    return report


def _ingest_discussion(
    session: Session, repo: Repository, discussion: Discussion
) -> tuple[int, int]:
    """Returns (objects_written, bodies_changed)."""
    objects = 0
    changed = 0

    obj = upsert.upsert_source_object(
        session,
        repo_id=repo.id,
        kind="discussion",
        native_id=discussion.node_id,
        number=discussion.number,
        url=discussion.url,
        title=discussion.title,
        state=discussion.resolution_signal,
        author=discussion.author,
        author_association=discussion.author_association,
        category=discussion.category,
        source_created_at=discussion.created_at,
        source_updated_at=discussion.updated_at,
        source_closed_at=discussion.closed_at,
        extra={
            "labels": discussion.labels,
            "upvotes": discussion.upvotes,
            "category_answerable": discussion.category_answerable,
            "answer_chosen_at": (
                discussion.answer_chosen_at.isoformat() if discussion.answer_chosen_at else None
            ),
            "comment_total": discussion.comment_total,
            # Honest coverage marker: we did not necessarily read the whole thread.
            "comments_truncated": discussion.comments_truncated,
        },
    )
    objects += 1
    if _ingest_body(
        session,
        repo=repo,
        obj=obj,
        body=discussion.body,
        source_updated_at=discussion.updated_at,
    ):
        changed += 1

    comment_objects: dict[str, int] = {}
    for comment in discussion.comments:
        c_obj = upsert.upsert_source_object(
            session,
            repo_id=repo.id,
            kind="discussion_comment",
            native_id=comment.node_id,
            url=comment.url,
            parent_id=obj.id,
            author=comment.author,
            author_association=comment.author_association,
            state="answer" if comment.is_answer else "comment",
            source_created_at=comment.created_at,
            source_updated_at=comment.updated_at,
            extra={
                "discussion_number": discussion.number,
                "upvotes": comment.upvotes,
                "parent_comment": comment.parent_comment_id,
                "replies_truncated": comment.replies_truncated,
            },
        )
        comment_objects[comment.node_id] = c_obj.id
        objects += 1
        if _ingest_body(
            session,
            repo=repo,
            obj=c_obj,
            body=comment.body,
            source_updated_at=comment.updated_at,
        ):
            changed += 1

        if comment.is_answer:
            # github_native: the maintainer marked this answer upstream.
            # It says the thread was answered, not that any code was fixed.
            upsert.assert_relation(
                session,
                repo_id=repo.id,
                relation_type="ANSWERED_BY",
                src_object_id=obj.id,
                dst_object_id=c_obj.id,
                derivation="github_native",
                evidence={
                    "marked_answer": True,
                    "answer_chosen_at": (
                        discussion.answer_chosen_at.isoformat()
                        if discussion.answer_chosen_at
                        else None
                    ),
                },
            )
    return objects, changed


def sync_discussions(
    session: Session,
    repo: Repository,
    profile: RepoProfile,
    client: GitHubClient,
    surfaces: RepoSurfaces,
    *,
    settings: Settings,
    full: bool = False,
    max_items: int | None = None,
    categories: list[str] | None = None,
    progress: ProgressFn = _noop,
) -> SourceReport:
    report = SourceReport(source="discussions")
    started = time.monotonic()
    state = upsert.mark_sync_start(session, repo.id, "discussions")
    watermark = None if full else state.watermark
    limit = settings.max_discussions_per_run if max_items is None else max_items

    # Scope: answerable categories (Q&A) are where troubleshooting actually happens.
    wanted = categories or surfaces.answerable_categories()
    category_ids = [
        c["id"] for c in surfaces.discussion_categories if not wanted or c["name"] in wanted
    ] or [None]

    highest_seen = watermark
    try:
        for category_id in category_ids:
            for discussion in iter_discussions(
                client,
                profile.owner,
                profile.name,
                page_size=settings.discussion_page_size,
                comments_per_page=20,
                replies_per_comment=5,
                updated_after=watermark,
                max_items=limit,
                category_id=category_id,
            ):
                objects, changed = _ingest_discussion(session, repo, discussion)
                report.objects += objects
                report.changed += changed
                if discussion.updated_at and (
                    highest_seen is None or discussion.updated_at > highest_seen
                ):
                    highest_seen = discussion.updated_at
                if report.objects % 200 == 0:
                    session.commit()
                    progress(f"discussions: {report.objects} objects written")

        session.flush()
        # A capped run is deliberately incomplete: say so instead of claiming completeness.
        capped = bool(limit) and report.objects >= limit
        report.status = "degraded" if capped else "complete"
        report.detail = {
            "categories": wanted or ["<all>"],
            "watermark_used": watermark.isoformat() if watermark else None,
            "capped": capped,
        }
        upsert.mark_sync_success(
            session,
            repo.id,
            "discussions",
            watermark=highest_seen,
            objects_seen=report.objects,
            full=full and not capped,
            status="degraded" if capped else "complete",
            stats=report.detail,
        )
        progress(f"discussions: {report.objects} objects ({report.changed} changed)")
    except Exception as exc:  # noqa: BLE001
        report.status = "failed"
        report.error = str(exc)
        session.rollback()
        upsert.mark_sync_failure(session, repo.id, "discussions", str(exc))
        session.commit()
        progress(f"discussions: FAILED {exc}")

    report.duration_s = time.monotonic() - started
    return report


def resolve_referenced_commits(
    session: Session,
    repo: Repository,
    git: GitRepo,
    *,
    limit: int = 500,
    progress: ProgressFn = _noop,
) -> SourceReport:
    """Materialise commits that upstream text actually pointed at.

    The spec forbids ingesting every commit. We only pull in the ones a
    discussion, comment or release note explicitly referenced.
    """
    from repo_troubleshooter.store.models import RelationAssertion

    report = SourceReport(source="commits")
    started = time.monotonic()
    upsert.mark_sync_start(session, repo.id, "commits")

    try:
        refs = session.scalars(
            select(RelationAssertion.dst_ref)
            .where(
                RelationAssertion.repo_id == repo.id,
                RelationAssertion.dst_ref.startswith("commit:"),
            )
            .distinct()
            .limit(limit)
        ).all()

        resolved = 0
        unknown = 0
        for ref in refs:
            if not ref:
                continue
            sha = ref.split(":", 1)[1]
            info = git.commit_info(sha)
            if info is None:
                # Hex-looking tokens in logs (session ids, digests) are not commits.
                # Downgrade instead of deleting: the text really did contain them.
                session.execute(
                    sa_update(RelationAssertion)
                    .where(
                        RelationAssertion.repo_id == repo.id,
                        RelationAssertion.dst_ref == ref,
                    )
                    .values(
                        confidence="low",
                        evidence=RelationAssertion.evidence.op("||")(
                            cast({"commit_resolvable": False}, JSONB)
                        ),
                    )
                )
                unknown += 1
                continue
            upsert.upsert_commit(
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
            resolved += 1

        report.objects = resolved
        report.detail = {"referenced": len(refs), "resolved": resolved, "unresolvable": unknown}
        report.status = "complete"
        upsert.mark_sync_success(
            session, repo.id, "commits", objects_seen=resolved, stats=report.detail
        )
        progress(f"commits: {resolved} resolved, {unknown} unresolvable")
    except Exception as exc:  # noqa: BLE001
        report.status = "failed"
        report.error = str(exc)
        session.rollback()
        upsert.mark_sync_failure(session, repo.id, "commits", str(exc))
        session.commit()
        progress(f"commits: FAILED {exc}")

    report.duration_s = time.monotonic() - started
    return report


def backfill_discussions(
    session: Session,
    repo: Repository,
    profile: RepoProfile,
    client: GitHubClient,
    surfaces: RepoSurfaces,
    *,
    settings: Settings,
    page_budget: int = 4,
    categories: list[str] | None = None,
    progress: ProgressFn = _noop,
) -> SourceReport:
    """Walk older discussions a few pages at a time, resuming where it stopped.

    The incremental sync only ever reaches back to its watermark. This walks
    forward through history instead, storing the GraphQL cursor after every page
    so an interrupted run resumes rather than restarts. It never bypasses the
    rate limit: the page budget is the whole point, and a run that stops with
    pages remaining reports `degraded`, never `complete`.
    """
    source = "discussions_backfill"
    report = SourceReport(source=source)
    started = time.monotonic()
    state = upsert.mark_sync_start(session, repo.id, source)
    stats = dict(state.stats or {})
    cursor: str | None = stats.get("cursor")
    exhausted = bool(stats.get("exhausted"))

    if exhausted:
        report.status = "complete"
        report.detail = {"exhausted": True, "pages_this_run": 0}
        upsert.mark_sync_success(session, repo.id, source, stats=stats)
        report.duration_s = time.monotonic() - started
        progress("backfill: already exhausted; nothing older to fetch")
        return report

    wanted = categories or surfaces.answerable_categories()
    category_ids = [
        c["id"] for c in surfaces.discussion_categories if not wanted or c["name"] in wanted
    ] or [None]

    pages_seen = 0
    last_cursor: str | None = cursor
    reached_end = False

    def record_page(end_cursor: str | None, pages: int) -> None:
        nonlocal last_cursor, pages_seen, reached_end
        pages_seen = pages
        if end_cursor is None:
            reached_end = True
        else:
            last_cursor = end_cursor

    try:
        for category_id in category_ids:
            for discussion in iter_discussions(
                client,
                profile.owner,
                profile.name,
                page_size=settings.discussion_page_size,
                comments_per_page=20,
                replies_per_comment=5,
                category_id=category_id,
                start_cursor=cursor,
                page_budget=page_budget,
                on_page=record_page,
            ):
                objects, changed = _ingest_discussion(session, repo, discussion)
                report.objects += objects
                report.changed += changed
            if reached_end:
                break

        session.flush()
        stats.update(
            {
                "cursor": None if reached_end else last_cursor,
                "exhausted": reached_end,
                "pages_last_run": pages_seen,
                "page_budget": page_budget,
                "categories": wanted or ["<all>"],
            }
        )
        report.status = "complete" if reached_end else "degraded"
        report.detail = stats
        upsert.mark_sync_success(
            session,
            repo.id,
            source,
            objects_seen=report.objects,
            status="complete" if reached_end else "degraded",
            stats=stats,
        )
        progress(
            f"backfill: {pages_seen} page(s), {report.objects} objects"
            + (" - history exhausted" if reached_end else " - more remain")
        )
    except Exception as exc:  # noqa: BLE001
        report.status = "failed"
        report.error = str(exc)
        session.rollback()
        upsert.mark_sync_failure(session, repo.id, source, str(exc))
        session.commit()
        progress(f"backfill: FAILED {exc}")

    report.duration_s = time.monotonic() - started
    return report


def build_signatures(
    session: Session, repo: Repository, *, progress: ProgressFn = _noop
) -> SourceReport:
    """Mine symptom signatures for everything currently stored."""
    report = SourceReport(source="signatures")
    started = time.monotonic()
    upsert.mark_sync_start(session, repo.id, "signatures")
    try:
        stats = build_for_repository(session, repo, progress=progress)
        report.objects = stats.objects
        report.changed = stats.rows_written
        report.detail = stats.to_json()
        report.status = "complete"
        upsert.mark_sync_success(
            session, repo.id, "signatures", objects_seen=stats.objects, stats=stats.to_json()
        )
        progress(f"signatures: {stats.objects} objects, {stats.rows_written} features")
    except Exception as exc:  # noqa: BLE001
        report.status = "failed"
        report.error = str(exc)
        session.rollback()
        upsert.mark_sync_failure(session, repo.id, "signatures", str(exc))
        session.commit()
        progress(f"signatures: FAILED {exc}")
    report.duration_s = time.monotonic() - started
    return report


# --- entry point ------------------------------------------------------------


def sync_repository(
    profile: RepoProfile,
    *,
    settings: Settings | None = None,
    full: bool = False,
    max_discussions: int | None = None,
    include_docs: bool = True,
    include_git: bool = True,
    backfill_pages: int = 0,
    progress: ProgressFn = _noop,
) -> SyncReport:
    settings = settings or get_settings()
    report = SyncReport(repo=profile.repo, started_at=dt.datetime.now(dt.UTC))

    with GitHubClient(settings) as client:
        progress(f"probing {profile.repo} ...")
        surfaces = probe_repository(client, profile.owner, profile.name)
        report.surfaces = surfaces.to_json()
        progress(
            f"surfaces: primary={surfaces.primary_support_surface} "
            f"discussions={surfaces.discussion_count} issues={surfaces.issue_count} "
            f"prs={surfaces.pull_request_count} releases={surfaces.release_count}"
        )

        git: GitRepo | None = None
        clone_path = clone_path_for(profile, settings)

        with session_scope() as session:
            repo = upsert.upsert_repository(
                session,
                owner=profile.owner,
                name=profile.name,
                host=profile.host,
                default_branch=surfaces.default_branch,
                clone_path=str(clone_path),
                profile_name=profile.repo,
                surfaces=surfaces.to_json(),
            )
            repo_id = repo.id

        if include_git:
            git_report = SourceReport(source="git")
            started = time.monotonic()
            try:
                progress(f"git: mirroring into {clone_path} (first run clones, later runs fetch)")
                git = GitRepo.ensure(clone_path, profile.resolved_clone_url())
                git.fetch()
                tags = git.list_tags()
                git_report.objects = len(tags)
                git_report.status = "complete"
                git_report.detail = {"clone_path": str(clone_path), "tags": len(tags)}
                with session_scope() as session:
                    upsert.mark_sync_success(
                        session,
                        repo_id,
                        "git",
                        objects_seen=len(tags),
                        full=True,
                        stats=git_report.detail,
                    )
                progress(f"git: {len(tags)} tags")
            except Exception as exc:  # noqa: BLE001
                git = None
                git_report.status = "failed"
                git_report.error = str(exc)
                with session_scope() as session:
                    upsert.mark_sync_failure(session, repo_id, "git", str(exc))
                progress(f"git: FAILED {exc}")
            git_report.duration_s = time.monotonic() - started
            report.sources["git"] = git_report

        with session_scope() as session:
            repo = _require_repository(session, repo_id)
            report.sources["releases"] = sync_releases(
                session, repo, profile, client, git, progress=progress
            )

        if include_docs and git is not None:
            with session_scope() as session:
                repo = _require_repository(session, repo_id)
                report.sources["docs"] = sync_docs(session, repo, profile, git, progress=progress)

        if surfaces.discussions_enabled and profile.support_surfaces.discussions is not False:
            with session_scope() as session:
                repo = _require_repository(session, repo_id)
                report.sources["discussions"] = sync_discussions(
                    session,
                    repo,
                    profile,
                    client,
                    surfaces,
                    settings=settings,
                    full=full,
                    max_items=max_discussions,
                    progress=progress,
                )

            if backfill_pages:
                with session_scope() as session:
                    repo = _require_repository(session, repo_id)
                    report.sources["discussions_backfill"] = backfill_discussions(
                        session,
                        repo,
                        profile,
                        client,
                        surfaces,
                        settings=settings,
                        page_budget=backfill_pages,
                        progress=progress,
                    )

        if git is not None:
            with session_scope() as session:
                repo = _require_repository(session, repo_id)
                report.sources["commits"] = resolve_referenced_commits(
                    session, repo, git, progress=progress
                )

        # Symptom signatures are what lets a paraphrase find an incident, so they
        # are part of ingest, not a separate manual step.
        with session_scope() as session:
            repo = _require_repository(session, repo_id)
            report.sources["signatures"] = build_signatures(session, repo, progress=progress)

    report.finished_at = dt.datetime.now(dt.UTC)
    return report
