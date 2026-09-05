"""Public diagnosis contract.

This is the black-box interface: the CLI and (later) MCP both speak exactly
this. Two rules shape it.

* **Privacy is bounded at the door.** The request carries versions, an error
  string and config *key names*. Anything that looks like a secret is redacted
  before it is stored or logged, and raw logs/config require an explicit opt-in.
* **Confidence belongs to a claim, not to the answer.** There is no
  ``answer_confidence`` field, by design.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

Status = Literal["confirmed", "probable", "insufficient_evidence", "conflicting"]
ReportKind = Literal["failure", "question", "idea", "unknown"]
AnchorKind = Literal[
    "error",
    "structural",
    "subject_package",
    "subject_path",
    "subject_module",
]
ActionType = Literal[
    "upgrade",
    "downgrade",
    "migrate",
    "config_change",
    "workaround",
    "collect_more_info",
    "abstain",
]
ClaimType = Literal["symptom_match", "change", "affected_in", "released_in", "action", "conflict"]
Confidence = Literal["high", "medium", "low"]
# How we know: stated outright / provable by git / observed once / concluded by us.
Basis = Literal["explicit", "deterministic", "observed", "inferred"]

# --- privacy ----------------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "<redacted:github-token>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<redacted:api-key>"),
    (re.compile(r"\bxox[abps]-[A-Za-z0-9-]{10,}"), "<redacted:slack-token>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "<redacted:jwt>",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|token|authorization|cookie)\b"
            r"\s*[:=]\s*['\"]?[^\s'\"]{6,}"
        ),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<redacted:email>"),
    # Home directories leak the user's name even when the path itself is harmless.
    (re.compile(r"(?i)\b[A-Za-z]:\\Users\\[^\\\s]+"), r"C:\\Users\\<user>"),
    (re.compile(r"(?i)/(?:home|Users)/[^/\s]+"), "/home/<user>"),
)


def redact(text: str | None) -> str | None:
    """Strip credentials and personal paths. Applied before storage or logging."""
    if not text:
        return text
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# --- request ----------------------------------------------------------------


class PluginSpec(BaseModel):
    name: str
    version: str | None = None


class StructuredAnchor(BaseModel):
    """One user-supplied, checkable fact used to reject wrong candidates.

    An anchor narrows retrieval only. It is deliberately not an authorization
    source: a report can contain the exact same error code while still being
    about a different failure, and no prose-derived fact may authorise a
    version-changing recommendation.
    """

    kind: AnchorKind
    value: str = Field(min_length=1, max_length=300)

    @field_validator("value")
    @classmethod
    def _normalise_value(cls, value: str) -> str:
        normalised = " ".join(value.strip().lower().split())
        if not normalised:
            raise ValueError("anchor value must not be blank")
        return normalised


class ReportAssessment(BaseModel):
    """Whether the input is sufficiently evidenced as a failure report.

    This is intentionally a report-quality classification, not an incident
    identity claim. It may stop a candidate proposal, but it never accepts an
    incident and never authorises an action.
    """

    kind: ReportKind = "unknown"
    basis: Literal["declared", "observed", "insufficient"] = "insufficient"
    retrieval_allowed: bool = False
    observed_evidence: list[str] = Field(default_factory=list)
    rationale: str = ""


class Understanding(BaseModel):
    """What the tool took the report to be saying, in the user's own terms.

    Shallow parsing of English will misread some reports; that is not fixable
    by more rules. What makes a misreading survivable is showing it. Everything
    the gate acted on appears here, separated by where it came from - what the
    user stated as fields, and what was read out of prose - so a wrong reading
    is visible before it becomes a recommendation rather than after.
    """

    #: Packages the user stated as fields.
    packages_stated: list[str] = Field(default_factory=list)
    #: Packages found in the local workspace. Presence is context, never proof
    #: that one of them failed and never an authorization source.
    workspace_packages: list[str] = Field(default_factory=list)
    #: Packages read out of prose, by the role the reading gave them.
    failing: list[str] = Field(default_factory=list)
    used: list[str] = Field(default_factory=list)
    cleared: list[str] = Field(default_factory=list)
    contradictory: list[str] = Field(default_factory=list)
    role_undetermined: list[str] = Field(default_factory=list)
    #: Packages and claims found inside quoted material, which is shown rather
    #: than asserted and never carries identity on its own.
    quoted_packages: list[str] = Field(default_factory=list)
    #: Sentences that state a condition in words the reader could not classify.
    unread_claims: list[str] = Field(default_factory=list)
    #: Explicit, structured facts supplied to reject incompatible candidates.
    #: They narrow candidate identity only; they are not action authority.
    identity_anchors: list[StructuredAnchor] = Field(default_factory=list)
    report_assessment: ReportAssessment | None = None
    core_version: str | None = None
    runtime: str | None = None
    os: str | None = None
    context_sources: dict[str, str] = Field(default_factory=dict)
    context_warnings: list[str] = Field(default_factory=list)
    #: The incident this reading points at. The digest has always covered it,
    #: but a digest is not something a person can check - agreeing to a reading
    #: that does not say which incident it matched is not an informed answer.
    incident_title: str | None = None
    incident_url: str | None = None
    #: Why it was accepted: the identity rule, and the features both sides
    #: share. This is the reasoning the agreement is really about.
    identity_rule: str | None = None
    shared_evidence: dict[str, list[str]] = Field(default_factory=dict)
    #: The upstream artifacts behind the proposal - the thread, the commit that
    #: changed it, the release that first carried the change.
    evidence: list[str] = Field(default_factory=list)
    #: What this answer would recommend if the reading is right.
    proposed_action: str | None = None
    proposed_target: str | None = None
    #: Identifies *this* reading and *this* proposal. A confirmation carries it
    #: back, so agreeing to one reading cannot authorise a different one.
    digest: str = ""


class Authorization(BaseModel):
    """Whether anything authorised recommending a change, and what.

    Retrieval and identity may run on free text. Recommending that someone
    change what they run may not: that authority comes from what the user
    stated in structured fields, or from confirming the reading back to them.
    Without one of those the answer stops at a proposal.
    """

    authorized: bool = False
    #: "structured_package" today; "confirmed" once the echo channel exists.
    source: str | None = None
    #: What would have been recommended, kept so the proposal is still visible.
    proposed_action: str | None = None
    proposed_target: str | None = None
    missing: list[str] = Field(default_factory=list)
    #: True when confirming the echoed reading would authorise the proposal.
    requires_confirmation: bool = False


class DiagnosisRequest(BaseModel):
    """What the user's machine may send. Nothing here is free-form telemetry."""

    repo: str
    error: str | None = None
    question: str | None = None
    #: Optional caller declaration. The default is deliberately unknown rather
    #: than assuming every upstream post is an incident report.
    report_kind: ReportKind = "unknown"
    core_version: str | None = None
    runtime: str | None = None  # e.g. "node 24.11.1"
    os: str | None = None  # e.g. "windows"
    # Safe metadata detected by the local CLI. These fields explain the echo;
    # they do not participate in action authorization.
    detected_packages: list[str] = Field(default_factory=list)
    context_sources: dict[str, str] = Field(default_factory=dict)
    context_warnings: list[str] = Field(default_factory=list)
    # The packages the user says they are running, stated as fields rather than
    # left to be read out of prose. Prose can be misread - a retraction missed,
    # a quoted ticket taken as the reporter's own - and a misreading that can
    # authorise an action is a wrong action. A misreading that can only *find*
    # a candidate is a wrong suggestion, which the user sees and rejects.
    packages: list[str] = Field(default_factory=list)
    identity_anchors: list[StructuredAnchor] = Field(default_factory=list)
    #: The digest of a reading the user has agreed with, echoed back from a
    #: previous answer. Confirming is the second authorisation source: it says
    #: "yes, that is my situation", about one specific reading.
    confirm: str | None = None
    plugins: list[PluginSpec] = Field(default_factory=list)
    # Key NAMES only. Values are never requested.
    config_keys: list[str] = Field(default_factory=list)
    # Explicit opt-in before any raw log text is retained.
    allow_raw_logs: bool = False

    @field_validator("error", "question", mode="after")
    @classmethod
    def _redact_free_text(cls, value: str | None) -> str | None:
        return redact(value)

    @field_validator("config_keys", mode="after")
    @classmethod
    def _keys_only(cls, values: list[str]) -> list[str]:
        # Defence in depth: if a caller sends key=value, keep only the key.
        return [v.split("=", 1)[0].strip() for v in values if v.strip()]

    def runtime_name_version(self) -> tuple[str | None, str | None]:
        if not self.runtime:
            return None, None
        match = re.match(r"\s*([A-Za-z][\w.+-]*)\s*[@ ]?\s*v?([\d][\w.+-]*)?", self.runtime)
        if not match:
            return self.runtime.strip().lower() or None, None
        name = (match.group(1) or "").lower() or None
        return name, match.group(2)

    def environment_json(self) -> dict[str, Any]:
        name, version = self.runtime_name_version()
        return {
            "repo": self.repo,
            "core_version": self.core_version,
            "runtime": self.runtime,
            "runtime_name": name,
            "runtime_version": version,
            "os": (self.os or "").lower() or None,
            "plugins": [p.model_dump() for p in self.plugins],
            "config_keys": self.config_keys,
            "detected_packages": self.detected_packages,
            "context_sources": self.context_sources,
            "context_warnings": self.context_warnings,
            "report_kind": self.report_kind,
            "identity_anchors": [anchor.model_dump() for anchor in self.identity_anchors],
        }


# --- response ---------------------------------------------------------------


class EvidenceRef(BaseModel):
    """A citation the caller can resolve with `get-evidence`."""

    id: str
    source_type: str
    locator: str
    url: str | None = None
    role: str = "context"
    source_event_time: dt.datetime | None = None
    knowledge_available_time: dt.datetime | None = None
    excerpt: str | None = None


class Claim(BaseModel):
    type: ClaimType
    value: str
    confidence: Confidence
    basis: Basis
    evidence_ids: list[str] = Field(default_factory=list)

    @property
    def supported(self) -> bool:
        return bool(self.evidence_ids)


class RecommendedAction(BaseModel):
    type: ActionType
    target: str | None = None
    rationale: str | None = None
    confidence: Confidence = "low"
    evidence_ids: list[str] = Field(default_factory=list)


class StageReport(BaseModel):
    """The three-stage contract, made explicit in the public output.

    ``retrieved_candidate`` is only "worth checking"; it never means a match.
    ``accepted_same_incident`` is the only thing that may set
    ``incident.matched``. ``actionable_incident`` is the only thing that may
    produce upgrade / downgrade / config_change / workaround.
    """

    retrieved_candidates: int = 0
    accepted_same_incident: bool = False
    actionable_incident: bool = False
    # Why stage 2 or stage 3 stopped, by class, with no candidate identity leaked.
    rejected_candidates: dict[str, int] = Field(default_factory=dict)
    stopped_at: str = "retrieved_candidate"


class IncidentSummary(BaseModel):
    """Populated only once `accepted_same_incident` passes."""

    matched: bool = False
    incident_id: str | None = None
    title: str | None = None
    url: str | None = None
    symptom_signature: str | None = None
    matched_tokens: list[str] = Field(default_factory=list)
    score: float = 0.0
    resolution_signal: str | None = None
    identity_rule: str | None = None
    shared_features: dict[str, list[str]] = Field(default_factory=dict)


class DiagnosisResponse(BaseModel):
    status: Status
    environment: dict[str, Any] = Field(default_factory=dict)
    stages: StageReport = Field(default_factory=StageReport)
    incident: IncidentSummary = Field(default_factory=IncidentSummary)
    applicability: dict[str, Any] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    recommended_action: RecommendedAction
    conflicts: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    data_as_of: dt.datetime | None = None
    sync_health: str = "unknown"
    coverage_notes: list[str] = Field(default_factory=list)
    fingerprint: dict[str, Any] = Field(default_factory=dict)
    provider: str = "deterministic"
    authorization: Authorization = Field(default_factory=Authorization)
    report_assessment: ReportAssessment = Field(default_factory=ReportAssessment)
    understood: Understanding | None = None

    # Actions that change what the user runs. Only stage 3 may produce these.
    VERSION_ACTIONS: ClassVar[frozenset[str]] = frozenset(
        {"upgrade", "downgrade", "migrate", "config_change", "workaround"}
    )

    @property
    def unsupported_claims(self) -> list[Claim]:
        return [c for c in self.claims if not c.supported]

    @property
    def proposes_change(self) -> bool:
        return self.recommended_action.type in self.VERSION_ACTIONS

    def revoke_incident(self, reason: str) -> None:
        """Withdraw the whole incident, not just one citation."""
        self.incident = IncidentSummary(matched=False)
        self.stages.accepted_same_incident = False
        self.stages.actionable_incident = False
        self.stages.stopped_at = "revoked_by_verifier"
        self.claims = []
        self.status = "insufficient_evidence"
        self.recommended_action = RecommendedAction(
            type="collect_more_info", rationale=reason, confidence="low"
        )
        if reason not in self.missing_information:
            self.missing_information.append(reason)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
