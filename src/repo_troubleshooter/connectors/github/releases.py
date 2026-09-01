"""Release / tag ingestion.

Releases matter for one reason only: they let us say which commits a user's
installed version already contains. ``tagCommit.oid`` is taken from GraphQL
because REST's ``target_commitish`` is often a branch name, not the tagged
commit.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass

from repo_troubleshooter.connectors.github.client import GitHubClient

RELEASES_QUERY = """
query Releases($owner: String!, $name: String!, $first: Int!, $after: String) {
  rateLimit { limit remaining resetAt cost }
  repository(owner: $owner, name: $name) {
    releases(first: $first, after: $after,
             orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        name
        tagName
        url
        description
        isPrerelease
        isDraft
        createdAt
        publishedAt
        tagCommit { oid }
      }
    }
  }
}
"""


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class ReleaseRecord:
    node_id: str
    tag_name: str
    name: str | None
    url: str | None
    body: str
    is_prerelease: bool
    is_draft: bool
    published_at: dt.datetime | None
    commit_sha: str | None


def iter_releases(
    client: GitHubClient, owner: str, name: str, *, page_size: int = 50, max_pages: int = 40
) -> Iterator[ReleaseRecord]:
    cursor: str | None = None
    pages = 0
    while pages < max_pages:
        data = client.graphql(
            RELEASES_QUERY,
            {"owner": owner, "name": name, "first": page_size, "after": cursor},
        )
        conn = ((data.get("repository") or {}).get("releases")) or {}
        nodes = conn.get("nodes") or []
        for node in nodes:
            yield ReleaseRecord(
                node_id=node["id"],
                tag_name=node["tagName"],
                name=node.get("name"),
                url=node.get("url"),
                body=node.get("description") or "",
                is_prerelease=bool(node.get("isPrerelease")),
                is_draft=bool(node.get("isDraft")),
                published_at=_parse_ts(node.get("publishedAt") or node.get("createdAt")),
                commit_sha=(node.get("tagCommit") or {}).get("oid"),
            )
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return
        cursor = page_info.get("endCursor")
        pages += 1
