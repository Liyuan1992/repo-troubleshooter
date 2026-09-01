"""Release containment.

The single most misused fact in this domain. This module answers exactly one
question - "is commit C an ancestor of tag T?" - and labels the answer as such.
It never upgrades that into "the bug is fixed in T".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.store.models import Release, ReleaseContainment, Repository
from repo_troubleshooter.sync.upsert import upsert_containment
from repo_troubleshooter.versions import semver

CONTAINMENT_MEANING = (
    "Commit containment proves the change is present in the tagged tree. "
    "It does not by itself prove the runtime symptom is resolved."
)


@dataclass
class ContainmentResult:
    commit_sha: str
    resolved_sha: str | None
    containing: list[str] = field(default_factory=list)
    not_containing: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    first_release_containing: str | None = None
    first_stable_release_containing: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def commit_known(self) -> bool:
        return self.resolved_sha is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "commit": self.commit_sha,
            "resolved_sha": self.resolved_sha,
            "containing": self.containing,
            "not_containing": self.not_containing,
            "unknown": self.unknown,
            "first_release_containing": self.first_release_containing,
            "first_stable_release_containing": self.first_stable_release_containing,
            "meaning": CONTAINMENT_MEANING,
        }


def _release_sort_key(release: Release) -> tuple[int, bool, Any, Any]:
    key = semver.sort_key(release.version_norm or release.tag_name)
    published = release.published_at
    return (key[0], key[1] is None, key[1], published or release.observed_at)


def compute_containment(
    session: Session,
    repo: Repository,
    commit_sha: str,
    *,
    git: GitRepo | None = None,
    persist: bool = True,
) -> ContainmentResult:
    """Evaluate a commit against every known release of the repository."""
    if git is None:
        if not repo.clone_path:
            raise ValueError(f"{repo.full_name} has no local mirror; run a sync first")
        git = GitRepo(repo.clone_path)
    resolved = git.resolve_ref(commit_sha)
    result = ContainmentResult(commit_sha=commit_sha, resolved_sha=resolved)
    releases = list(
        session.scalars(
            select(Release).where(Release.repo_id == repo.id, Release.is_draft.is_(False))
        )
    )
    releases.sort(key=_release_sort_key)

    if resolved is None:
        result.unknown = [r.tag_name for r in releases]
        return result

    for release in releases:
        contains, git_result = git.is_ancestor(resolved, release.tag_name)
        transcript = git_result.transcript()
        if contains is None:
            result.unknown.append(release.tag_name)
            continue
        if contains:
            result.containing.append(release.tag_name)
        else:
            result.not_containing.append(release.tag_name)
        result.evidence.append({"tag": release.tag_name, "contains": contains, **transcript})
        if persist:
            upsert_containment(
                session,
                repo_id=repo.id,
                release_id=release.id,
                commit_sha=resolved,
                contains=contains,
                evidence=transcript,
            )

    ordered_containing = [r for r in releases if r.tag_name in set(result.containing)]
    if ordered_containing:
        result.first_release_containing = ordered_containing[0].tag_name
        stable = [r for r in ordered_containing if not r.is_prerelease]
        if stable:
            result.first_stable_release_containing = stable[0].tag_name
    return result


def cached_containment(session: Session, repo: Repository, commit_sha: str) -> ContainmentResult:
    """Read the persisted answer without touching git. Empty result when uncached."""
    rows = session.execute(
        select(ReleaseContainment, Release)
        .join(Release, Release.id == ReleaseContainment.release_id)
        .where(
            ReleaseContainment.repo_id == repo.id,
            ReleaseContainment.commit_sha.startswith(commit_sha),
        )
    ).all()
    result = ContainmentResult(commit_sha=commit_sha, resolved_sha=None)
    releases: list[Release] = []
    for containment, release in rows:
        result.resolved_sha = containment.commit_sha
        releases.append(release)
        (result.containing if containment.contains else result.not_containing).append(
            release.tag_name
        )
    containing = sorted(
        (r for r in releases if r.tag_name in set(result.containing)), key=_release_sort_key
    )
    if containing:
        result.first_release_containing = containing[0].tag_name
        stable = [r for r in containing if not r.is_prerelease]
        if stable:
            result.first_stable_release_containing = stable[0].tag_name
    return result


def version_already_contains(
    result: ContainmentResult, user_version: str | None
) -> tuple[bool | None, str]:
    """Does the user's version already include the change?

    Returns (verdict, explanation). ``None`` means unresolved - never guess,
    the caller must degrade to ``unresolved_version`` rather than recommend an
    upgrade the user has already performed.
    """
    if result.first_release_containing is None:
        return None, "no known release contains this commit yet"
    user = semver.parse(user_version)
    first = semver.parse(result.first_release_containing)
    if user is None:
        return None, f"could not parse user version {user_version!r}"
    if first is None:
        return None, f"could not parse release {result.first_release_containing!r}"
    if user >= first:
        return True, (
            f"user version {user} >= {first} (first release containing the change). "
            + CONTAINMENT_MEANING
        )
    return False, f"user version {user} < {first} (first release containing the change)"
