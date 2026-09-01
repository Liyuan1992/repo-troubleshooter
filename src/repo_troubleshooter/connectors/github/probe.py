"""Support-surface probe.

Repositories do not share a data shape. deepseek-harness has Discussions and
no Issues and no public PRs; vLLM has Issues and PRs and no Q&A discussions.
Nothing downstream may assume ``Issue -> PR -> Commit`` exists, so we detect
what is actually there and record it on the repository row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from repo_troubleshooter.connectors.github.client import GitHubClient

PROBE_QUERY = """
query Probe($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner
    url
    description
    isArchived
    isPrivate
    primaryLanguage { name }
    defaultBranchRef { name }
    hasIssuesEnabled
    hasDiscussionsEnabled
    hasWikiEnabled
    issues { totalCount }
    pullRequests { totalCount }
    discussions { totalCount }
    releases { totalCount }
    refs(refPrefix: "refs/tags/") { totalCount }
    discussionCategories(first: 25) {
      nodes { id name slug isAnswerable description }
    }
  }
}
"""


@dataclass
class RepoSurfaces:
    full_name: str
    url: str | None = None
    default_branch: str | None = None
    language: str | None = None
    archived: bool = False
    issues_enabled: bool = False
    discussions_enabled: bool = False
    issue_count: int = 0
    pull_request_count: int = 0
    discussion_count: int = 0
    release_count: int = 0
    tag_count: int = 0
    discussion_categories: list[dict[str, Any]] = field(default_factory=list)

    @property
    def primary_support_surface(self) -> str:
        """Where users actually report problems in this repository."""
        if self.discussion_count > max(self.issue_count, 50):
            return "discussions"
        if self.issue_count > 0:
            return "issues"
        if self.discussion_count > 0:
            return "discussions"
        return "none"

    @property
    def has_pr_chain(self) -> bool:
        """Whether Discussion/Issue -> PR -> Commit is even possible here."""
        return self.pull_request_count > 0

    def answerable_categories(self) -> list[str]:
        return [c["name"] for c in self.discussion_categories if c.get("isAnswerable")]

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["primary_support_surface"] = self.primary_support_surface
        payload["has_pr_chain"] = self.has_pr_chain
        return payload


def probe_repository(client: GitHubClient, owner: str, name: str) -> RepoSurfaces:
    data = client.graphql(PROBE_QUERY, {"owner": owner, "name": name})
    repo = data.get("repository")
    if not repo:
        raise ValueError(f"repository {owner}/{name} not found or not visible to this token")
    return RepoSurfaces(
        full_name=repo["nameWithOwner"],
        url=repo.get("url"),
        default_branch=(repo.get("defaultBranchRef") or {}).get("name"),
        language=(repo.get("primaryLanguage") or {}).get("name"),
        archived=bool(repo.get("isArchived")),
        issues_enabled=bool(repo.get("hasIssuesEnabled")),
        discussions_enabled=bool(repo.get("hasDiscussionsEnabled")),
        issue_count=(repo.get("issues") or {}).get("totalCount", 0),
        pull_request_count=(repo.get("pullRequests") or {}).get("totalCount", 0),
        discussion_count=(repo.get("discussions") or {}).get("totalCount", 0),
        release_count=(repo.get("releases") or {}).get("totalCount", 0),
        tag_count=(repo.get("refs") or {}).get("totalCount", 0),
        discussion_categories=(repo.get("discussionCategories") or {}).get("nodes", []) or [],
    )
