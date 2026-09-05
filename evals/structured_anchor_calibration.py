"""Calibrate the structured identity-constraint channel against adjudicated pairs.

This is not an automatic semantic evaluation.  Each anchor below was selected
by reading the source report and is a fact a cooperative caller could provide
as a structured field.  The check proves the narrow contract only:

* each fact rejects its adjudicated wrong candidate; and
* the channel preserves one independently reviewed positive in each repository.

Passing this script never permits anchors to authorise a recommendation.  It
also does not claim that free text can reliably choose these anchors; that is a
separate semantic-model evaluation, still open.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.diagnosis.contract import AnchorKind, StructuredAnchor
from repo_troubleshooter.relations.signatures import load_features
from repo_troubleshooter.retrieval.anchors import mismatches
from repo_troubleshooter.store.db import session_scope
from repo_troubleshooter.store.models import Repository, SourceObject


@dataclass(frozen=True)
class CalibrationCase:
    repo: str
    report: int
    candidate: int
    anchor: StructuredAnchor
    expected: bool


def _anchor(kind: AnchorKind, value: str) -> StructuredAnchor:
    return StructuredAnchor(kind=kind, value=value)


def _reject(
    repo: str, report: int, candidate: int, kind: AnchorKind, value: str
) -> CalibrationCase:
    return CalibrationCase(repo, report, candidate, _anchor(kind, value), False)


def _preserve(
    repo: str, report: int, candidate: int, kind: AnchorKind, value: str
) -> CalibrationCase:
    return CalibrationCase(repo, report, candidate, _anchor(kind, value), True)


# Four adjudicated DeepSeek false proposals plus all ten adjudicated vLLM false
# proposals.  The query report contains the anchor; the accepted wrong candidate
# does not.  The number is intentionally fixed to the adjudication documents,
# not generated from whatever candidate the current ranker happens to return.
DEEPSEEK = "deepseek-ai/deepseek-harness"
VLLM = "vllm-project/vllm"

WRONG_PAIRS: tuple[CalibrationCase, ...] = (
    _reject(DEEPSEEK, 4954, 4066, "structural", "agents.create"),
    _reject(DEEPSEEK, 1648, 1507, "structural", "inputbar.tsx"),
    _reject(DEEPSEEK, 4967, 4666, "structural", "agent.followup"),
    _reject(DEEPSEEK, 4563, 4167, "error", "default_prepared_session_cache_size"),
    _reject(VLLM, 19668, 32732, "error", "timeouterror"),
    _reject(VLLM, 52065, 49922, "error", "cuda_error_illegal_address"),
    _reject(VLLM, 43174, 54219, "error", "zerodivisionerror"),
    _reject(VLLM, 53089, 54096, "error", "pytorch_home"),
    _reject(VLLM, 53477, 52049, "structural", "collect_env.py"),
    _reject(VLLM, 44318, 38884, "structural", "/rank_0_0/model"),
    _reject(VLLM, 40791, 40919, "error", "assertionerror"),
    _reject(VLLM, 41287, 54723, "error", "keyerror"),
    _reject(VLLM, 54526, 54723, "structural", "config.eagle3speculatorconfig"),
    _reject(VLLM, 38884, 36010, "structural", "abstract.py"),
)

POSITIVES: tuple[CalibrationCase, ...] = (
    _preserve(DEEPSEEK, 5084, 5084, "structural", "__dsh_boot__"),
    _preserve(VLLM, 6461, 6461, "structural", "/metrics"),
)


def _object(session: Session, repo: Repository, number: int) -> SourceObject:
    obj = session.scalar(
        select(SourceObject).where(SourceObject.repo_id == repo.id, SourceObject.number == number)
    )
    if obj is None:
        raise RuntimeError(f"{repo.full_name} #{number} is not present in the local evidence store")
    return obj


def run() -> dict[str, object]:
    cases = (*WRONG_PAIRS, *POSITIVES)
    results: list[dict[str, object]] = []
    with session_scope() as session:
        repositories = {
            name: session.scalar(select(Repository).where(Repository.full_name == name))
            for name in {case.repo for case in cases}
        }
        for case in cases:
            repo = repositories[case.repo]
            if repo is None:
                raise RuntimeError(
                    f"{case.repo} is not synced; run `rt prepare` or `rt sync` first"
                )
            # Check that the factual anchor genuinely appears in the source
            # report, not merely that it happens to reject the candidate.
            query = _object(session, repo, case.report)
            candidate = _object(session, repo, case.candidate)
            query_mismatch = mismatches((case.anchor,), load_features(session, query.id))
            candidate_mismatch = mismatches((case.anchor,), load_features(session, candidate.id))
            passed = not query_mismatch and (not candidate_mismatch) is case.expected
            results.append(
                {
                    "repo": case.repo,
                    "report": case.report,
                    "candidate": case.candidate,
                    "anchor": case.anchor.model_dump(),
                    "expected_candidate_match": case.expected,
                    "passed": passed,
                }
            )

    failures = [case for case in results if not case["passed"]]
    return {
        "wrong_pair_count": len(WRONG_PAIRS),
        "positive_count": len(POSITIVES),
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "cases": results,
    }


if __name__ == "__main__":
    payload = run()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(1 if payload["failed"] else 0)
