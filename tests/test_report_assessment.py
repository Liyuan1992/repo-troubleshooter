"""Contract tests for conservative failure-report classification."""

from __future__ import annotations

from repo_troubleshooter.diagnosis.contract import DiagnosisRequest, ReportKind
from repo_troubleshooter.diagnosis.intent import assess_report
from repo_troubleshooter.fingerprint.features import SymptomFeatures


def _request(*, report_kind: ReportKind = "unknown") -> DiagnosisRequest:
    return DiagnosisRequest(repo="owner/repo", report_kind=report_kind)


def test_declared_question_never_starts_incident_retrieval() -> None:
    assessment = assess_report(
        _request(report_kind="question"), SymptomFeatures(error={"typeerror"})
    )

    assert assessment.kind == "question"
    assert assessment.basis == "declared"
    assert assessment.retrieval_allowed is False


def test_declared_failure_is_transparent_override_for_an_underspecified_report() -> None:
    assessment = assess_report(_request(report_kind="failure"), SymptomFeatures())

    assert assessment.kind == "failure"
    assert assessment.basis == "declared"
    assert assessment.retrieval_allowed is True


def test_nonquoted_machine_marker_and_symptom_is_observed_failure() -> None:
    assessment = assess_report(
        _request(), SymptomFeatures(structural={"__dsh_boot__"}, behavior={"absent:entry"})
    )

    assert assessment.kind == "failure"
    assert assessment.basis == "observed"
    assert assessment.observed_evidence == ["machine marker with symptom"]


def test_unbound_but_explicit_failing_state_is_still_an_observed_failure() -> None:
    assessment = assess_report(
        _request(),
        SymptomFeatures(
            unresolved_state_assertions=[{"state": "failing", "cue": "failed to mount"}]
        ),
    )

    assert assessment.kind == "failure"
    assert assessment.basis == "observed"


def test_quoted_error_does_not_turn_a_question_into_a_failure_report() -> None:
    features = SymptomFeatures(error={"typeerror"}, unquoted=SymptomFeatures())

    assessment = assess_report(_request(), features)

    assert assessment.kind == "unknown"
    assert assessment.retrieval_allowed is False
