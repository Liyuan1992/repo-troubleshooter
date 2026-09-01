"""Discussion ingestion.

For deepseek-harness this is the only user-facing support surface, so it is the
spine of the incident layer. We page by UPDATED_AT DESC so an incremental run
can stop as soon as it crosses the stored watermark, and we always report how
much of each thread we actually captured (``comments_truncated``) instead of
pretending we saw the whole thread.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from repo_troubleshooter.connectors.github.client import GitHubClient

_COMMENT_FIELDS = """
  id
  databaseId
  url
  body
  createdAt
  updatedAt
  lastEditedAt
  isAnswer
  upvoteCount
  authorAssociation
  author { login }
"""

DISCUSSIONS_QUERY = """
query Discussions($owner: String!, $name: String!, $first: Int!, $after: String,
                  $comments: Int!, $replies: Int!, $categoryId: ID) {
  rateLimit { limit remaining resetAt cost }
  repository(owner: $owner, name: $name) {
    discussions(first: $first, after: $after, categoryId: $categoryId,
                orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        number
        title
        body
        url
        createdAt
        updatedAt
        lastEditedAt
        closed
        closedAt
        isAnswered
        answerChosenAt
        upvoteCount
        authorAssociation
        author { login }
        category { name slug isAnswerable }
        labels(first: 20) { nodes { name } }
        answer { id }
        comments(first: $comments) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes {
            __COMMENT_FIELDS__
            replies(first: $replies) {
              totalCount
              pageInfo { hasNextPage endCursor }
              nodes { __COMMENT_FIELDS__ }
            }
          }
        }
      }
    }
  }
}
""".replace("__COMMENT_FIELDS__", _COMMENT_FIELDS)

COMMENTS_PAGE_QUERY = """
query DiscussionComments($id: ID!, $first: Int!, $after: String, $replies: Int!) {
  rateLimit { limit remaining resetAt cost }
  node(id: $id) {
    ... on Discussion {
      comments(first: $first, after: $after) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          __COMMENT_FIELDS__
          replies(first: $replies) {
            totalCount
            pageInfo { hasNextPage endCursor }
            nodes { __COMMENT_FIELDS__ }
          }
        }
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


@dataclass
class Comment:
    node_id: str
    body: str
    url: str | None
    author: str | None
    author_association: str | None
    is_answer: bool
    upvotes: int
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
    parent_comment_id: str | None = None
    replies_truncated: bool = False


@dataclass
class Discussion:
    node_id: str
    number: int
    title: str
    body: str
    url: str
    category: str | None
    category_answerable: bool
    author: str | None
    author_association: str | None
    labels: list[str]
    closed: bool
    is_answered: bool
    answer_comment_id: str | None
    upvotes: int
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
    closed_at: dt.datetime | None
    answer_chosen_at: dt.datetime | None
    comments: list[Comment] = field(default_factory=list)
    comment_total: int = 0
    comments_truncated: bool = False
    comments_cursor: str | None = None

    @property
    def resolution_signal(self) -> str:
        """Raw upstream state only. 'answered' is not 'fixed' - see spec section 6."""
        if self.is_answered:
            return "answer_marked"
        if self.closed:
            return "closed_without_answer"
        return "open"


def _comment_from_node(node: dict[str, Any], parent_id: str | None = None) -> Comment:
    replies = node.get("replies") or {}
    return Comment(
        node_id=node["id"],
        body=node.get("body") or "",
        url=node.get("url"),
        author=(node.get("author") or {}).get("login"),
        author_association=node.get("authorAssociation"),
        is_answer=bool(node.get("isAnswer")),
        upvotes=node.get("upvoteCount") or 0,
        created_at=_parse_ts(node.get("createdAt")),
        updated_at=_parse_ts(node.get("lastEditedAt") or node.get("updatedAt")),
        parent_comment_id=parent_id,
        replies_truncated=bool((replies.get("pageInfo") or {}).get("hasNextPage")),
    )


def _discussion_from_node(node: dict[str, Any]) -> Discussion:
    comments_conn = node.get("comments") or {}
    comments: list[Comment] = []
    for c_node in comments_conn.get("nodes") or []:
        comment = _comment_from_node(c_node)
        comments.append(comment)
        for r_node in (c_node.get("replies") or {}).get("nodes") or []:
            comments.append(_comment_from_node(r_node, parent_id=comment.node_id))

    category = node.get("category") or {}
    return Discussion(
        node_id=node["id"],
        number=node["number"],
        title=node.get("title") or "",
        body=node.get("body") or "",
        url=node.get("url") or "",
        category=category.get("name"),
        category_answerable=bool(category.get("isAnswerable")),
        author=(node.get("author") or {}).get("login"),
        author_association=node.get("authorAssociation"),
        labels=[n["name"] for n in (node.get("labels") or {}).get("nodes", [])],
        closed=bool(node.get("closed")),
        is_answered=bool(node.get("isAnswered")),
        answer_comment_id=(node.get("answer") or {}).get("id"),
        upvotes=node.get("upvoteCount") or 0,
        created_at=_parse_ts(node.get("createdAt")),
        updated_at=_parse_ts(node.get("lastEditedAt") or node.get("updatedAt")),
        closed_at=_parse_ts(node.get("closedAt")),
        answer_chosen_at=_parse_ts(node.get("answerChosenAt")),
        comments=comments,
        comment_total=comments_conn.get("totalCount") or 0,
        comments_truncated=bool((comments_conn.get("pageInfo") or {}).get("hasNextPage")),
        comments_cursor=(comments_conn.get("pageInfo") or {}).get("endCursor"),
    )


def iter_discussions(
    client: GitHubClient,
    owner: str,
    name: str,
    *,
    page_size: int = 50,
    comments_per_page: int = 30,
    replies_per_comment: int = 10,
    updated_after: dt.datetime | None = None,
    max_items: int = 0,
    category_id: str | None = None,
) -> Iterator[Discussion]:
    """Yield discussions newest-updated first.

    ``updated_after`` stops the walk at the incremental watermark;
    ``max_items`` (0 = unlimited) is the scope guard for a first sync;
    ``category_id`` narrows the walk to one discussion category (e.g. Q&A),
    which is how we keep the first sync inside the GraphQL point budget.
    """
    cursor: str | None = None
    yielded = 0
    while True:
        data = client.graphql(
            DISCUSSIONS_QUERY,
            {
                "owner": owner,
                "name": name,
                "first": page_size,
                "after": cursor,
                "comments": comments_per_page,
                "replies": replies_per_comment,
                "categoryId": category_id,
            },
        )
        conn = ((data.get("repository") or {}).get("discussions")) or {}
        nodes = conn.get("nodes") or []
        if not nodes:
            return

        for node in nodes:
            discussion = _discussion_from_node(node)
            if updated_after and discussion.updated_at and discussion.updated_at <= updated_after:
                return
            yield discussion
            yielded += 1
            if max_items and yielded >= max_items:
                return

        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return
        cursor = page_info.get("endCursor")


def fetch_remaining_comments(
    client: GitHubClient,
    discussion: Discussion,
    *,
    page_size: int = 50,
    replies_per_comment: int = 10,
    max_pages: int = 20,
) -> list[Comment]:
    """Drain the comment pages a discussion page could not carry."""
    extra: list[Comment] = []
    cursor = discussion.comments_cursor
    pages = 0
    while cursor and pages < max_pages:
        data = client.graphql(
            COMMENTS_PAGE_QUERY,
            {
                "id": discussion.node_id,
                "first": page_size,
                "after": cursor,
                "replies": replies_per_comment,
            },
        )
        conn = ((data.get("node") or {}).get("comments")) or {}
        for c_node in conn.get("nodes") or []:
            comment = _comment_from_node(c_node)
            extra.append(comment)
            for r_node in (c_node.get("replies") or {}).get("nodes") or []:
                extra.append(_comment_from_node(r_node, parent_id=comment.node_id))
        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        pages += 1
    return extra
