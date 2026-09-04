from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy import select

from repo_troubleshooter.connectors.github.work_items import (
    fetch_work_item,
    iter_work_items,
)
from repo_troubleshooter.evidence.packet import resolve
from repo_troubleshooter.fingerprint.features import extract
from repo_troubleshooter.profiles.loader import RepoProfile, load_profile
from repo_troubleshooter.store.models import RelationAssertion
from repo_troubleshooter.sync import upsert
from repo_troubleshooter.sync.orchestrator import _ingest_work_item, sync_work_items


def _node(kind: str, number: int) -> dict[str, Any]:
    common: dict[str, Any] = {
        "id": f"NODE-{kind}-{number}",
        "number": number,
        "title": "metrics endpoint missing",
        "body": "GET /metrics returns 404",
        "url": f"https://github.com/example/project/{kind}/{number}",
        "state": "CLOSED" if kind == "issue" else "MERGED",
        "createdAt": "2024-07-18T00:00:00Z",
        "updatedAt": "2024-07-19T00:00:00Z",
        "closedAt": "2024-07-19T00:00:00Z",
        "authorAssociation": "CONTRIBUTOR",
        "author": {"login": "reporter"},
        "labels": {"nodes": [{"name": "bug"}]},
        "comments": {
            "totalCount": 1,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "id": f"COMMENT-{kind}-{number}",
                    "body": "confirmed",
                    "url": None,
                    "createdAt": "2024-07-18T01:00:00Z",
                    "updatedAt": "2024-07-18T01:00:00Z",
                    "lastEditedAt": None,
                    "authorAssociation": "MEMBER",
                    "author": {"login": "maintainer"},
                }
            ],
        },
    }
    if kind == "issue":
        common["stateReason"] = "COMPLETED"
    else:
        common.update(
            {
                "mergedAt": "2024-07-19T00:00:00Z",
                "mergeCommit": {"oid": "a" * 40},
                "baseRefName": "main",
                "headRefName": "fix-metrics",
                "closingIssuesReferences": {
                    "totalCount": 1,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "NODE-issue-6461",
                            "number": 6461,
                            "url": "https://github.com/example/project/issues/6461",
                        }
                    ],
                },
            }
        )
    return common


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = iter(responses)

    def graphql(self, _query: str, _variables: dict[str, Any]) -> dict[str, Any]:
        return next(self.responses)


def test_iter_issues_stops_at_watermark() -> None:
    newer = _node("issue", 2)
    older = _node("issue", 1)
    older["updatedAt"] = "2024-07-01T00:00:00Z"
    client = FakeClient(
        [
            {
                "repository": {
                    "issues": {
                        "nodes": [newer, older],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        ]
    )
    items = list(
        iter_work_items(
            client,  # type: ignore[arg-type]
            "example",
            "project",
            "issue",
            updated_after=dt.datetime(2024, 7, 10, tzinfo=dt.UTC),
        )
    )
    assert [item.number for item in items] == [2]
    assert items[0].comments[0].author == "maintainer"


def test_direct_pr_seed_preserves_native_resolution_edges() -> None:
    client = FakeClient(
        [{"repository": {"issue": None, "pullRequest": _node("pull_request", 6463)}}]
    )
    item = fetch_work_item(
        client,  # type: ignore[arg-type]
        "example",
        "project",
        "pull_request",
        6463,
    )
    assert item is not None
    assert item.merge_commit_sha == "a" * 40
    assert item.closing_issues[0][1] == 6461


@pytest.mark.db
def test_ingest_pr_records_github_native_close_and_merge(session) -> None:  # noqa: ANN001
    repo = upsert.upsert_repository(session, owner="rt-test", name="work-items")
    issue_client = FakeClient([{"repository": {"issue": _node("issue", 6461)}}])
    issue = fetch_work_item(
        issue_client,  # type: ignore[arg-type]
        "example",
        "project",
        "issue",
        6461,
    )
    assert issue is not None
    _, _, issue_obj = _ingest_work_item(session, repo, issue)

    pr_client = FakeClient([{"repository": {"pullRequest": _node("pull_request", 6463)}}])
    pr = fetch_work_item(
        pr_client,  # type: ignore[arg-type]
        "example",
        "project",
        "pull_request",
        6463,
    )
    assert pr is not None
    _, _, pr_obj = _ingest_work_item(session, repo, pr)
    relations = list(
        session.scalars(
            select(RelationAssertion).where(RelationAssertion.src_object_id == pr_obj.id)
        )
    )
    closes = next(row for row in relations if row.relation_type == "CLOSES")
    merged = next(row for row in relations if row.relation_type == "PR_MERGED_AS")
    assert closes.dst_object_id == issue_obj.id
    assert closes.derivation == "github_native"
    assert merged.dst_ref == f"commit:{'a' * 40}"
    resolved_pr = resolve(session, repo, "ev:pull_request:6463")
    assert resolved_pr is not None
    assert resolved_pr.role == "change"


@pytest.mark.db
def test_bounded_issue_sync_stays_degraded_after_a_success(session) -> None:  # noqa: ANN001
    repo = upsert.upsert_repository(
        session,
        owner="rt-test",
        name="bounded-work-items",
        surfaces={"issue_count": 10, "pull_request_count": 0},
    )
    profile = RepoProfile(repo="rt-test/bounded-work-items")
    client = FakeClient(
        [
            {
                "repository": {
                    "issues": {
                        "nodes": [_node("issue", 1)],
                        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                    }
                }
            }
        ]
    )
    report = sync_work_items(
        session,
        repo,
        profile,
        client,  # type: ignore[arg-type]
        "issues",
        max_items=1,
    )
    assert report.status == "degraded"
    assert report.detail["stored_top_level"] == 1
    assert report.detail["upstream_total"] == 10
    assert report.detail["coverage_partial"] is True


@pytest.mark.db
def test_complete_issue_walk_stays_degraded_when_comments_are_truncated(session) -> None:  # noqa: ANN001
    repo = upsert.upsert_repository(
        session,
        owner="rt-test",
        name="truncated-work-items",
        surfaces={"issue_count": 1, "pull_request_count": 0},
    )
    profile = RepoProfile(repo="rt-test/truncated-work-items")
    node = _node("issue", 1)
    node["comments"]["totalCount"] = 21
    node["comments"]["pageInfo"]["hasNextPage"] = True
    client = FakeClient(
        [
            {
                "repository": {
                    "issues": {
                        "nodes": [node],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        ]
    )
    report = sync_work_items(
        session,
        repo,
        profile,
        client,  # type: ignore[arg-type]
        "issues",
        max_items=0,
    )
    assert report.status == "degraded"
    assert report.detail["coverage_partial"] is False
    assert report.detail["nested_partial"] is True
    assert report.detail["truncated_comment_threads"] == 1


def test_vllm_profile_has_a_frozen_reviewed_chain() -> None:
    profile = load_profile("vllm")
    assert profile.seed_objects.issues == [6461]
    assert profile.seed_objects.pull_requests == [6463]
    reviewed = profile.reviewed_incidents[0]
    assert reviewed.first_release == "v0.5.3"
    assert reviewed.reported_versions == ["0.5.2"]
    assert profile.holdout.report_kinds == ["issue"]
    assert profile.holdout.assumed_version == "0.5.2"
    assert profile.holdout.case_version_source == "report"
    assert len(profile.holdout.report_version_patterns) == 3
    assert "/metrics" in (profile.holdout.positive_control_error or "")


def test_http_route_is_a_structural_symptom_feature() -> None:
    assert "/metrics" in extract("GET /metrics returns 404 Not Found").structural
