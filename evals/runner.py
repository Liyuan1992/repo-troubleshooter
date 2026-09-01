"""Evaluation runner.

Loads the YAML case files, drives the same engine the CLI drives, and checks
each expectation. It reports failures rather than raising, so one report shows
the whole picture.

Nothing here special-cases a repository, a discussion number or an evidence id
beyond what a case file states as its own expectation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from repo_troubleshooter.diagnosis.contract import DiagnosisRequest, DiagnosisResponse
from repo_troubleshooter.diagnosis.engine import diagnose

CASES_DIR = Path(__file__).parent / "cases"


@dataclass
class CaseResult:
    case_id: str
    kind: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "case": self.case_id,
            "kind": self.kind,
            "passed": self.passed,
            "failures": self.failures,
            "observed": self.observed,
        }


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]

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


def _target_is_real(response: DiagnosisResponse) -> bool:
    target = response.recommended_action.target
    if not target:
        return True
    return any(e.source_type == "release" and e.locator == target for e in response.evidence)


def run_incidents(session: Session) -> list[CaseResult]:
    spec = _load("incidents.yaml")
    defaults = spec.get("defaults", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        query = {**defaults, **case["query"]}
        request = DiagnosisRequest(
            repo=spec["repo"],
            error=query.get("error"),
            core_version=query.get("core_version"),
            runtime=query.get("runtime"),
            os=query.get("os"),
        )
        response, _packet, _debug = diagnose(request, session, persist=False)
        expect = case.get("expect", {})
        failures = _check_common(expect, response)
        if expect.get("target_must_be_real_release") and not _target_is_real(response):
            failures.append(
                f"action target {response.recommended_action.target!r} is not a cited release"
            )
        results.append(
            CaseResult(case["id"], "incident", not failures, failures, _observed(response))
        )
    return results


def run_negatives(session: Session) -> list[CaseResult]:
    spec = _load("negatives.yaml")
    defaults = spec.get("defaults", {})
    shared = spec.get("expect_all", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        request = DiagnosisRequest(
            repo=spec["repo"],
            error=case["error"],
            core_version=defaults.get("core_version"),
            runtime=defaults.get("runtime"),
            os=defaults.get("os"),
        )
        response, _packet, _debug = diagnose(request, session, persist=False)
        failures = _check_common(shared, response)
        # A negative must not produce a version action backed by borrowed evidence.
        if response.recommended_action.target:
            failures.append(
                f"negative case produced a target release {response.recommended_action.target!r}"
            )
        results.append(
            CaseResult(case["id"], "negative", not failures, failures, _observed(response))
        )
    return results


def run_perturbations(session: Session) -> list[CaseResult]:
    spec = _load("perturbations.yaml")
    base = spec.get("base_query", {})
    results: list[CaseResult] = []
    for case in spec.get("cases", []):
        request = DiagnosisRequest(
            repo=spec["repo"],
            error=base.get("error"),
            core_version=case.get("core_version"),
            runtime=case.get("runtime", base.get("runtime")),
            os=case.get("os", base.get("os")),
        )
        response, _packet, _debug = diagnose(request, session, persist=False)
        failures = _check_common(case.get("expect", {}), response)
        results.append(
            CaseResult(case["id"], "perturbation", not failures, failures, _observed(response))
        )
    return results


def run_all(session: Session) -> EvalReport:
    report = EvalReport()
    report.results.extend(run_incidents(session))
    report.results.extend(run_negatives(session))
    report.results.extend(run_perturbations(session))
    return report


def main() -> int:  # pragma: no cover - manual entry point
    from repo_troubleshooter.store.db import session_scope

    with session_scope() as session:
        report = run_all(session)
    out = Path(__file__).parent / "reports" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")
    print(json.dumps(report.to_json()["by_kind"], indent=2))
    print(f"{report.passed}/{len(report.results)} passed -> {out}")
    for failure in report.failed:
        print(f"  FAIL {failure.case_id}: {'; '.join(failure.failures)}")
    return 0 if not report.failed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
