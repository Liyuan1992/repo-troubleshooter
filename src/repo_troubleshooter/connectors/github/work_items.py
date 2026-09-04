"""GitHub Issues and pull-request ingestion.

Issues and pull requests are separate support surfaces in GraphQL.  They are
kept separate here as well: a shared number does not make an issue a PR, and a
PR body that merely mentions an issue is not the same as GitHub's native
``closingIssuesReferences`` edge.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

from repo_troubleshooter.connectors.github.client import GitHubClient

_COMMENT_FIELDS = """
  id
  databaseId
  url
  body
  createdAt
  updatedAt
  lastEditedAt
  authorAssociation
  author { login }
"""

ISSUES_QUERY = """
query Issues($owner: String!, $name: String!, $first: Int!, $after: String) {
  rateLimit { limit remaining resetAt cost }
  repository(owner: $owner, name: $name) {
    issues(first: $first, after: $after,
           orderBy: {field: UPDATED_AT, direction: DESC}, states: [OPEN, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number title body url state stateReason createdAt updatedAt closedAt
        authorAssociation author { login }
        labels(first: 20) { nodes { name } }
        comments(first: 20) {
          totalCount pageInfo { hasNextPage endCursor }
          nodes { __COMMENT_FIELDS__ }
        }
      }
    }
  }
}
""".replace("__COMMENT_FIELDS__", _COMMENT_FIELDS)

PULL_REQUESTS_QUERY = """
query PullRequests($owner: String!, $name: String!, $first: Int!, $after: String) {
  rateLimit { limit remaining resetAt cost }
  repository(owner: $owner, name: $name) {
    pullRequests(first: $first, after: $after,
                 orderBy: {field: UPDATED_AT, direction: DESC},
                 states: [OPEN, CLOSED, MERGED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number title body url state createdAt updatedAt closedAt mergedAt
        authorAssociation author { login }
        baseRefName headRefName
        mergeCommit { oid }
        labels(first: 20) { nodes { name } }
        closingIssuesReferences(first: 20) {
          totalCount pageInfo { hasNextPage endCursor }
          nodes { id number url }
        }
        comments(first: 20) {
          totalCount pageInfo { hasNextPage endCursor }
          nodes { __COMMENT_FIELDS__ }
        }
      }
    }
  }
}
""".replace("__COMMENT_FIELDS__", _COMMENT_FIELDS)

ISSUE_QUERY = """
query Issue($owner: String!, $name: String!, $number: Int!) {
  rateLimit { limit remaining resetAt cost }
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      id number title body url state stateReason createdAt updatedAt closedAt
      authorAssociation author { login }
      labels(first: 20) { nodes { name } }
      comments(first: 20) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { __COMMENT_FIELDS__ }
      }
    }
  }
}
""".replace("__COMMENT_FIELDS__", _COMMENT_FIELDS)

PULL_REQUEST_QUERY = """
query PullRequest($owner: String!, $name: String!, $number: Int!) {
  rateLimit { limit remaining resetAt cost }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id number title body url state createdAt updatedAt closedAt mergedAt
      authorAssociation author { login }
      baseRefName headRefName
      mergeCommit { oid }
      labels(first: 20) { nodes { name } }
      closingIssuesReferences(first: 20) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { id number url }
      }
      comments(first: 20) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { __COMMENT_FIELDS__ }
      }
    }
  }
}
""".replace("__COMMENT_FIELDS__", _COMMENT_FIELDS)


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class WorkItemComment:
    node_id: str
    body: str
    url: str | None
    author: str | None
    author_association: str | None
    created_at: dt.datetime | None
    updated_at: dt.datetime | None


@dataclass
class WorkItem:
    kind: Literal["issue", "pull_request"]
    node_id: str
    number: int
    title: str
    body: str
    url: str
    state: str
    author: str | None
    author_association: str | None
    labels: list[str]
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
    closed_at: dt.datetime | None
    comments: list[WorkItemComment] = field(default_factory=list)
    comment_total: int = 0
    comments_truncated: bool = False
    state_reason: str | None = None
    merged_at: dt.datetime | None = None
    merge_commit_sha: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    closing_issues: list[tuple[str, int, str | None]] = field(default_factory=list)
    closing_issues_truncated: bool = False


def _comment_from_node(node: dict[str, Any]) -> WorkItemComment:
    return WorkItemComment(
        node_id=node["id"],
        body=node.get("body") or "",
        url=node.get("url"),
        author=(node.get("author") or {}).get("login"),
        author_association=node.get("authorAssociation"),
        created_at=_parse_ts(node.get("createdAt")),
        updated_at=_parse_ts(node.get("lastEditedAt") or node.get("updatedAt")),
    )


def _work_item_from_node(node: dict[str, Any], kind: Literal["issue", "pull_request"]) -> WorkItem:
    comments = node.get("comments") or {}
    closing = node.get("closingIssuesReferences") or {}
    return WorkItem(
        kind=kind,
        node_id=node["id"],
        number=int(node["number"]),
        title=node.get("title") or "",
        body=node.get("body") or "",
        url=node.get("url") or "",
        state=(node.get("state") or "").lower(),
        state_reason=node.get("stateReason"),
        author=(node.get("author") or {}).get("login"),
        author_association=node.get("authorAssociation"),
        labels=[label["name"] for label in (node.get("labels") or {}).get("nodes", [])],
        created_at=_parse_ts(node.get("createdAt")),
        updated_at=_parse_ts(node.get("updatedAt")),
        closed_at=_parse_ts(node.get("closedAt")),
        merged_at=_parse_ts(node.get("mergedAt")),
        merge_commit_sha=(node.get("mergeCommit") or {}).get("oid"),
        base_ref=node.get("baseRefName"),
        head_ref=node.get("headRefName"),
        comments=[_comment_from_node(c) for c in comments.get("nodes") or []],
        comment_total=comments.get("totalCount") or 0,
        comments_truncated=bool((comments.get("pageInfo") or {}).get("hasNextPage")),
        closing_issues=[
            (str(issue["id"]), int(issue["number"]), issue.get("url"))
            for issue in closing.get("nodes") or []
        ],
        closing_issues_truncated=bool((closing.get("pageInfo") or {}).get("hasNextPage")),
    )


def iter_work_items(
    client: GitHubClient,
    owner: str,
    name: str,
    kind: Literal["issue", "pull_request"],
    *,
    page_size: int = 50,
    updated_after: dt.datetime | None = None,
    max_items: int = 0,
) -> Iterator[WorkItem]:
    """Yield newest-updated work items, bounded by a watermark and item cap."""
    query = ISSUES_QUERY if kind == "issue" else PULL_REQUESTS_QUERY
    connection = "issues" if kind == "issue" else "pullRequests"
    cursor: str | None = None
    yielded = 0
    while True:
        data = client.graphql(
            query,
            {"owner": owner, "name": name, "first": page_size, "after": cursor},
        )
        conn = ((data.get("repository") or {}).get(connection)) or {}
        nodes = conn.get("nodes") or []
        if not nodes:
            return
        for node in nodes:
            item = _work_item_from_node(node, kind)
            if updated_after and item.updated_at and item.updated_at <= updated_after:
                return
            yield item
            yielded += 1
            if max_items and yielded >= max_items:
                return
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return
        cursor = page_info.get("endCursor")


def fetch_work_item(
    client: GitHubClient,
    owner: str,
    name: str,
    kind: Literal["issue", "pull_request"],
    number: int,
) -> WorkItem | None:
    """Fetch a historical seed directly, without walking thousands of newer items."""
    query = ISSUE_QUERY if kind == "issue" else PULL_REQUEST_QUERY
    data = client.graphql(
        query,
        {"owner": owner, "name": name, "number": number},
    )
    repo = data.get("repository") or {}
    node = repo.get("issue" if kind == "issue" else "pullRequest")
    return _work_item_from_node(node, kind) if node else None
