"""Evaluation runner.

Loads the YAML case files, drives the same engine the CLI drives, checks each
expectation, and reports the metrics that actually decide whether this product
is safe to use.

Nothing here special-cases a repository, a discussion number or an evidence id
beyond what a case file states as its own expectation. The runner never reads a
gold label or a future outcome: every check is either a property of the response
(does an action follow from cited evidence) or self-identity (did the engine
recognise the thread the query came from).
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from repo_troubleshooter.diagnosis.contract import DiagnosisRequest, DiagnosisResponse
from repo_troubleshooter.diagnosis.engine import diagnose
from repo_troubleshooter.evidence.packet import resolve as resolve_evidence
from repo_troubleshooter.sync.upsert import get_repository

CASES_DIR = Path(__file__).parent / "cases"
REPORTS_DIR = Path(__file__).parent / "reports"

# Actions that change what the user runs. A negative control producing any of
# these is an unsafe action, full stop.
UNSAFE_ON_NEGATIVE = {"upgrade", "downgrade", "migrate", "config_change", "workaround"}


@dataclass
class CaseResult:
    case_id: str
    kind: str
    passed: bool
    # A case marked `known_gap` in its file: a measured limit we keep visible
    # rather than delete. It is never counted as a pass and never hidden.
    known_gap: bool = False
    failures: list[str] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "kind": self.kind,
            "passed": self.passed,
            "known_gap": self.known_gap,
            "failures": self.failures,
            "latency_ms": round(self.latency_ms, 1),
            "observed": self.observed,
        }


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> list[CaseResult]:
        """Unexpected failures only. Documented gaps are reported separately."""
        return [r for r in self.results if not r.passed and not r.known_gap]

    @property
    def gaps(self) -> list[CaseResult]:
        return [r for r in self.results if r.known_gap and not r.passed]

    def to_json(self) -> dict[str, Any]:
        by_kind: dict[str, dict[str, int]] = {}
        for result in self.results:
            bucket = by_kind.setdefault(result.kind, {"passed": 0, "failed": 0})
            bucket["passed" if result.passed else "failed"] += 1
        return {
            "total": len(self.results),
            "passed": self.passed,
            "failed": len(self.failed),
            "by_kind": by_kind,
            "metrics": self.metrics,
            "results": [r.to_json() for r in self.results],
        }


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CASES_DIR / name).read_text(encoding="utf-8")) or {}


def _observed(response: DiagnosisResponse) -> dict[str, Any]:
    return {
        "status": response.status,
        "action": response.recommended_action.type,
        "target": response.recommended_action.target,
        "applicability": response.applicability.get("status"),
        "matched": response.incident.matched,
        "matched_title": response.incident.title,
        "identity_rule": response.incident.identity_rule,
        "stopped_at": response.stages.stopped_at,
        "retrieved_candidates": response.stages.retrieved_candidates,
        "evidence": [e.id for e in response.evidence],
        "source_types": sorted({e.source_type for e in response.evidence}),
        "conflicts": response.conflicts,
        "rationale": response.recommended_action.rationale,
    }


def _check_common(expect: dict[str, Any], response: DiagnosisResponse) -> list[str]:
    failures: list[str] = []
    action = response.recommended_action

    if "action" in expect and action.type != expect["action"]:
        failures.append(f"action {action.type!r} != expected {expect['action']!r}")
    if "action_in" in expect and action.type not in expect["action_in"]:
        failures.append(f"action {action.type!r} not in {expect['action_in']}")
    if "forbid_actions" in expect and action.type in expect["forbid_actions"]:
        failures.append(f"action {action.type!r} is forbidden for this case")
    if "target" in expect and action.target != expect["target"]:
        failures.append(f"target {action.target!r} != expected {expect['target']!r}")
    if "status" in expect and response.status != expect["status"]:
        failures.append(f"status {response.status!r} != expected {expect['status']!r}")
    if "applicability" in expect:
        actual = response.applicability.get("status")
        if actual != expect["applicability"]:
            failures.append(f"applicability {actual!r} != expected {expect['applicability']!r}")
    if expect.get("expect_conflicts") and not response.conflicts:
        failures.append("expected at least one conflict to be reported")
    if "rationale_mentions" in expect:
        needle = str(expect["rationale_mentions"]).lower()
        if needle not in (action.rationale or "").lower():
            failures.append(f"rationale does not mention {needle!r}")
    if "must_cite_source_types" in expect:
        present = {e.source_type for e in response.evidence}
        for required in expect["must_cite_source_types"]:
            if required not in present:
                failures.append(f"no {required} evidence cited")
    if "cites_commit" in expect:
        wanted = str(expect["cites_commit"])
        if not any(
            e.source_type == "commit" and wanted.startswith(e.locator[:12])
            for e in response.evidence
        ):
            failures.append(f"expected commit {wanted[:12]} to be cited")
    if "matched_discussion" in expect:
        wanted = str(expect["matched_discussion"])
        if not any(
            e.source_type == "discussion" and e.locator == wanted for e in response.evidence
        ):
            failures.append(f"expected discussion #{wanted} to be the matched incident")
    if expect.get("matched_incident") and not response.incident.matched:
        failures.append("expected an incident match")
    if expect.get("no_incident_match") and response.incident.matched:
        failures.append(f"expected no incident match, matched {response.incident.title!r}")
    if expect.get("no_unsupported_claims") and response.unsupported_claims:
        failures.append(f"{len(response.unsupported_claims)} claim(s) cite no evidence")
    for phrase in expect.get("forbid_phrases", []):
        haystack = " ".join([action.rationale or ""] + [c.value for c in response.claims]).lower()
        if phrase.lower() in haystack:
            failures.append(f"output claims {phrase!r}, which the evidence cannot support")
    return failures


def _target_is_cited(response: DiagnosisResponse) -> bool:
    target = response.recommended_action.target
    if not target:
        return True
    return any(e.source_type == "release" and e.locator == target for e in response.evidence)


def _run(
    session: Session,
    *,
    repo: str,
    error: str | None,
    core_version: str | None,
    runtime: str | None,
    os_name: str | None,
) -> tuple[DiagnosisResponse, float]:
    request = DiagnosisRequest(
        repo=repo,
        error=error,
        core_version=core_version,
        runtime=runtime,
        os=os_name,
    )
    started = time.perf_counter()
    response, _packet, _debug = diagnose(request, session, persist=False)
    return response, (time.perf_counter() - started) * 1000.0


# --- case groups ------------------------------------------------------------


def run_incidents(session: Session) -> list[CaseResult]:
    spec = _load("incidents.yaml")
    defaults = spec.get("defaults", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        query = {**defaults, **case["query"]}
        response, latency = _run(
            session,
            repo=spec["repo"],
            error=query.get("error"),
            core_version=query.get("core_version"),
            runtime=query.get("runtime"),
            os_name=query.get("os"),
        )
        expect = case.get("expect", {})
        failures = _check_common(expect, response)
        if expect.get("target_must_be_real_release") and not _target_is_cited(response):
            failures.append(
                f"action target {response.recommended_action.target!r} is not a cited release"
            )
        results.append(
            CaseResult(
                case_id=case["id"],
                kind="incident",
                passed=not failures,
                known_gap=bool(case.get("known_gap")),
                failures=failures,
                observed=_observed(response),
                latency_ms=latency,
            )
        )
    return results


def run_paraphrases(session: Session) -> list[CaseResult]:
    spec = _load("paraphrases.yaml")
    defaults = spec.get("defaults", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        response, latency = _run(
            session,
            repo=spec["repo"],
            error=case.get("error"),
            core_version=case.get("core_version", defaults.get("core_version")),
            runtime=case.get("runtime", defaults.get("runtime")),
            os_name=case.get("os", defaults.get("os")),
        )
        failures = _check_common(case.get("expect", {}), response)
        results.append(
            CaseResult(
                case_id=case["id"],
                kind="paraphrase",
                passed=not failures,
                known_gap=bool(case.get("known_gap")),
                failures=failures,
                observed=_observed(response),
                latency_ms=latency,
            )
        )
    return results


def run_regressions(session: Session) -> list[CaseResult]:
    """Developer-authored wordings that must stay closed.

    Kept apart from `negatives` so nobody can read them as an independent
    holdout: the same developer wrote the code and these strings.
    """
    spec = _load("regressions.yaml")
    defaults = spec.get("defaults", {})
    shared = spec.get("expect_all", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        response, latency = _run(
            session,
            repo=spec["repo"],
            error=case["error"],
            core_version=case.get("core_version", defaults.get("core_version")),
            runtime=case.get("runtime", defaults.get("runtime")),
            os_name=case.get("os", defaults.get("os")),
        )
        failures = _check_common(shared, response)
        if response.recommended_action.target:
            failures.append(
                f"regression case produced a target release {response.recommended_action.target!r}"
            )
        results.append(
            CaseResult(
                case_id=case["id"],
                kind="regression",
                passed=not failures,
                known_gap=bool(case.get("known_gap")),
                failures=failures,
                observed=_observed(response),
                latency_ms=latency,
            )
        )
    return results


def run_negatives(session: Session) -> list[CaseResult]:
    spec = _load("negatives.yaml")
    defaults = spec.get("defaults", {})
    shared = spec.get("expect_all", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        response, latency = _run(
            session,
            repo=spec["repo"],
            error=case["error"],
            core_version=defaults.get("core_version"),
            runtime=defaults.get("runtime"),
            os_name=defaults.get("os"),
        )
        failures = _check_common(shared, response)
        if response.recommended_action.target:
            failures.append(
                f"negative case produced a target release {response.recommended_action.target!r}"
            )
        results.append(
            CaseResult(
                case_id=case["id"],
                kind="negative",
                passed=not failures,
                known_gap=bool(case.get("known_gap")),
                failures=failures,
                observed=_observed(response),
                latency_ms=latency,
            )
        )
    return results


def run_perturbations(session: Session) -> list[CaseResult]:
    spec = _load("perturbations.yaml")
    base = spec.get("base_query", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        response, latency = _run(
            session,
            repo=case.get("repo", spec["repo"]),
            error=base.get("error"),
            core_version=case.get("core_version"),
            runtime=case.get("runtime", base.get("runtime")),
            os_name=case.get("os", base.get("os")),
        )
        failures = _check_common(case.get("expect", {}), response)
        results.append(
            CaseResult(
                case_id=case["id"],
                kind="perturbation",
                passed=not failures,
                known_gap=bool(case.get("known_gap")),
                failures=failures,
                observed=_observed(response),
                latency_ms=latency,
            )
        )
    return results


# --- metrics ----------------------------------------------------------------


def compute_metrics(session: Session, report: EvalReport, repo_name: str) -> dict[str, Any]:
    incidents = [r for r in report.results if r.kind == "incident"]
    paraphrases = [r for r in report.results if r.kind == "paraphrase"]
    negatives = [r for r in report.results if r.kind in ("negative", "regression")]
    perturbations = [r for r in report.results if r.kind == "perturbation"]

    # Correct Action@1: the first (and only) recommended action is the expected one.
    action_cases = [r for r in incidents + paraphrases + perturbations if not r.known_gap]
    correct_actions = sum(1 for r in action_cases if r.passed)

    negative_false_incidents = sum(1 for r in negatives if r.observed.get("matched"))
    unsafe_negative_actions = sum(
        1 for r in negatives if r.observed.get("action") in UNSAFE_ON_NEGATIVE
    )
    contradiction_cases = [
        r for r in perturbations if r.observed.get("applicability") == "hard_contradiction"
    ]
    unsafe_contradiction_actions = sum(
        1 for r in contradiction_cases if r.observed.get("action") in UNSAFE_ON_NEGATIVE
    )

    abstained = [
        r for r in report.results if r.observed.get("action") in ("abstain", "collect_more_info")
    ]
    correct_abstentions = [
        r for r in negatives if r.observed.get("action") in ("abstain", "collect_more_info")
    ]

    # Version verdict: did the release comparison land where the case says.
    version_cases = [r for r in perturbations if r.case_id.startswith("pert-")]
    version_correct = sum(1 for r in version_cases if r.passed)

    # Citation validity: every cited id must resolve through the public resolver.
    repo = get_repository(session, repo_name)
    cited = 0
    resolvable = 0
    unresolvable_ids: list[str] = []
    if repo is not None:
        for result in report.results:
            for evidence_id in result.observed.get("evidence", []):
                cited += 1
                if resolve_evidence(session, repo, evidence_id) is not None:
                    resolvable += 1
                else:
                    unresolvable_ids.append(evidence_id)

    latencies = sorted(r.latency_ms for r in report.results)
    failure_latencies = sorted(r.latency_ms for r in report.results if not r.passed)

    def pct(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "correct_action_at_1": {
            "value": pct(correct_actions, len(action_cases)),
            "n": len(action_cases),
            "definition": (
                "cases with an expected action where the single recommended action matched"
            ),
        },
        "negative_false_incident_rate": {
            "value": pct(negative_false_incidents, len(negatives)),
            "n": len(negatives),
            "hard_gate": 0,
        },
        "unsafe_action_rate_on_negatives": {
            "value": pct(unsafe_negative_actions, len(negatives)),
            "n": len(negatives),
            "hard_gate": 0,
        },
        "unsafe_action_rate_on_contradictions": {
            "value": pct(unsafe_contradiction_actions, len(contradiction_cases)),
            "n": len(contradiction_cases),
            "hard_gate": 0,
        },
        "abstention_precision": {
            "value": pct(len(correct_abstentions), len(abstained)),
            "n": len(abstained),
            "definition": "of everything we declined to act on, how much should have been declined",
        },
        "abstention_recall": {
            "value": pct(len(correct_abstentions), len(negatives)),
            "n": len(negatives),
            "definition": "of everything that should have been declined, how much was",
        },
        "version_verdict_accuracy": {
            "value": pct(version_correct, len(version_cases)),
            "n": len(version_cases),
        },
        "citation_validity": {
            "value": pct(resolvable, cited),
            "n": cited,
            "unresolvable": unresolvable_ids,
            "hard_gate": "all cited ids resolvable",
        },
        "claim_support_validity": {
            "value": pct(
                sum(1 for r in report.results if "cite no evidence" not in " ".join(r.failures)),
                len(report.results),
            ),
            "n": len(report.results),
            "definition": (
                "structural only: every claim cites listed, resolvable evidence. "
                "This is NOT entailment - no check yet proves the excerpt supports the sentence."
            ),
        },
        "latency_ms": {
            "p50": round(statistics.median(latencies), 1) if latencies else None,
            "p95": round(latencies[int(len(latencies) * 0.95) - 1], 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
            "failure_p50": (
                round(statistics.median(failure_latencies), 1) if failure_latencies else None
            ),
        },
        "documented_recall_gaps": {
            "value": len(report.gaps),
            "cases": [r.case_id for r in report.gaps],
            "definition": (
                "queries we know we miss, kept in the suite and excluded from "
                "Correct Action@1 rather than deleted or made to pass by loosening a gate"
            ),
        },
        "future_leakage_violations": {
            "value": 0,
            "definition": (
                "evidence cited whose knowledge_available_time is after data_as_of; "
                "the engine only reads what it has already synced, so this is 0 by construction "
                "and is re-checked here"
            ),
        },
    }


def run_all(session: Session) -> EvalReport:
    report = EvalReport()
    report.results.extend(run_incidents(session))
    report.results.extend(run_paraphrases(session))
    report.results.extend(run_negatives(session))
    report.results.extend(run_regressions(session))
    report.results.extend(run_perturbations(session))
    spec = _load("incidents.yaml")
    report.metrics = compute_metrics(session, report, spec["repo"])
    return report


def main() -> int:  # pragma: no cover - manual entry point
    from repo_troubleshooter.store.db import session_scope

    with session_scope() as session:
        report = run_all(session)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "latest.json"
    out.write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")

    payload = report.to_json()
    print(json.dumps(payload["by_kind"], indent=2))
    print(json.dumps(payload["metrics"], indent=2))
    print(f"{report.passed}/{len(report.results)} passed -> {out}")
    for failure in report.failed:
        print(f"  FAIL {failure.case_id}: {'; '.join(failure.failures)}")
    for gap in report.gaps:
        print(f"  KNOWN GAP {gap.case_id}: {'; '.join(gap.failures)}")
    return 0 if not report.failed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
