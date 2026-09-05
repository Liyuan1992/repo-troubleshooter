"""Conservative classification of a report before incident retrieval.

This module intentionally does not contain an evolving list of ways somebody
can describe a bug.  The extractor already records the structured evidence it
can defend.  Here we decide only whether that evidence says the input is a
failure report at all.  An uncertain answer stops before a candidate proposal;
the caller can either add a concrete symptom or explicitly declare the input a
failure report.
"""

from __future__ import annotations

from repo_troubleshooter.diagnosis.contract import DiagnosisRequest, ReportAssessment
from repo_troubleshooter.fingerprint.features import SymptomFeatures


def assess_report(request: DiagnosisRequest, features: SymptomFeatures) -> ReportAssessment:
    """Assess report kind without inferring new prose semantics.

    A caller's structured declaration is transparent and takes precedence.
    Without one, use only already-extracted, non-quoted facts.  A standalone
    path, module, or symbol is intentionally insufficient: it says where code
    is, not that the reporter observed a failure.
    """
    if request.report_kind == "question":
        return ReportAssessment(
            kind="question",
            basis="declared",
            rationale="the caller marked this input as a question, not a failure report",
        )
    if request.report_kind == "idea":
        return ReportAssessment(
            kind="idea",
            basis="declared",
            rationale="the caller marked this input as an idea or request, not a failure report",
        )
    if request.report_kind == "failure":
        return ReportAssessment(
            kind="failure",
            basis="declared",
            retrieval_allowed=True,
            rationale="the caller explicitly marked this as a failure report",
        )

    # Quoted logs can retrieve a candidate, but the quotation cannot establish
    # that its reporter has the same failure.  The assessment follows that
    # same source boundary.
    stated = features.unquoted or features
    observed: list[str] = []
    if stated.error:
        observed.append("error code or exception")
    if stated.causes:
        observed.append("named failure mechanism")
    if stated.subject_packages and (stated.error or stated.causes or stated.behavior):
        observed.append("failed package with symptom")
    if any(assertion.get("state") == "failing" for assertion in stated.unresolved_state_assertions):
        observed.append("unbound observed failure state")
    # A concrete machine marker plus a reported symptom is a report witness;
    # neither one is enough on its own.  These are extractor output classes,
    # not a new phrase or verb list.
    if stated.structural and stated.behavior:
        observed.append("machine marker with symptom")

    if observed:
        return ReportAssessment(
            kind="failure",
            basis="observed",
            retrieval_allowed=True,
            observed_evidence=observed,
            rationale="the report contains non-quoted structured failure evidence",
        )

    return ReportAssessment(
        kind="unknown",
        basis="insufficient",
        rationale=(
            "the input does not establish an observed failure outside quoted material; "
            "it may be a question, idea, or an underspecified report"
        ),
    )
