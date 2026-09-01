"""The `accepted_same_incident` gate.

Retrieval answers "is this worth looking at". This module answers the much
harder question the product is actually judged on: **is this the same problem?**

Three rules, all learned from measured failures:

1. **Identity needs agreement across independent feature classes.** One rare
   word, one shared filename or one shared component is a coincidence. An error
   type plus a symbol, two symbols, or several behavioural facts plus component
   agreement are identity.
2. **A stated root cause is decisive.** If the reporter says the browser refused
   the script under a Content-Security-Policy directive, then a loader bug with
   a similar surface is *not* their problem, however well the words line up.
3. **Environment can only veto.** Everyone on Windows shares Windows; sharing it
   proves nothing. Runtime/OS is handled by the applicability gate, and never
   contributes to identity here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from repo_troubleshooter.fingerprint.features import SymptomFeatures

# Weight per class: what a shared feature of that class is worth.
CLASS_WEIGHTS = {
    "error": 3.0,
    "structural": 3.0,
    "behavior": 1.5,
    "component": 0.5,
}
MIN_IDENTITY_SCORE = 4.5


@dataclass
class IdentityVerdict:
    accepted: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    rejection: str | None = None
    shared: dict[str, list[str]] = field(default_factory=dict)
    rule: str | None = None
    conflicting_causes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "score": round(self.score, 2),
            "rule": self.rule,
            "rejection": self.rejection,
            "shared": self.shared,
            "reasons": self.reasons,
            "conflicting_causes": self.conflicting_causes,
        }


def _different_cause_reason(query_causes: set[str], candidate_causes: set[str]) -> str:
    stated = ", ".join(sorted(query_causes))
    reason = (
        f"the report states a specific failure mechanism ({stated}) "
        "that this candidate does not exhibit"
    )
    if candidate_causes:
        reason += f"; the candidate's mechanism is {', '.join(sorted(candidate_causes))}"
    return reason


def _weighted(values: set[str], weight: float, doc_freq: dict[str, int], corpus: int) -> float:
    """Sum class weights, discounted when a feature is common in this corpus.

    Without corpus statistics the class weight applies in full: not knowing how
    common a feature is must not silently make every match look weak.
    """
    total = 0.0
    for value in values:
        if corpus:
            frequency = max(1, doc_freq.get(value, 1))
            rarity = min(math.log(1.0 + corpus / frequency), 4.0) / 4.0
        else:
            rarity = 1.0
        total += weight * rarity
    return total


def evaluate(
    query: SymptomFeatures,
    candidate: SymptomFeatures,
    *,
    doc_freq: dict[str, int] | None = None,
    corpus_objects: int = 0,
) -> IdentityVerdict:
    """Decide whether a retrieved candidate is the *same incident* as the query."""
    doc_freq = doc_freq or {}

    shared_error = query.error & candidate.error
    shared_struct = query.structural & candidate.structural
    shared_behavior = query.behavior & candidate.behavior
    shared_component = query.component & candidate.component
    shared = {
        "error": sorted(shared_error),
        "structural": sorted(shared_struct),
        "behavior": sorted(shared_behavior),
        "component": sorted(shared_component),
    }

    # --- rule 2: an explicitly stated cause decides before anything else ---
    if query.causes:
        overlap = query.causes & candidate.causes
        if not overlap:
            return IdentityVerdict(
                accepted=False,
                score=0.0,
                rejection="different_root_cause",
                shared=shared,
                conflicting_causes=sorted(query.causes),
                reasons=[_different_cause_reason(query.causes, candidate.causes)],
            )

    score = (
        _weighted(shared_error, CLASS_WEIGHTS["error"], doc_freq, corpus_objects)
        + _weighted(shared_struct, CLASS_WEIGHTS["structural"], doc_freq, corpus_objects)
        + _weighted(shared_behavior, CLASS_WEIGHTS["behavior"], doc_freq, corpus_objects)
        + _weighted(shared_component, CLASS_WEIGHTS["component"], doc_freq, corpus_objects)
    )

    # --- rule 3: when both sides name subjects and none overlap ---
    # A shared error class is not identity if the things it happened to are
    # different: `ERESOLVE` for @acme/design-system and `ERESOLVE` for
    # @deepseek-ai/dsh are the same npm failure mode, not the same incident.
    subjects_disjoint = bool(query.structural) and bool(candidate.structural) and not shared_struct

    # --- rule 1: which combination of classes is enough to mean "same" ---
    rule: str | None = None
    if subjects_disjoint:
        # Only a full behavioural profile can carry identity across different subjects.
        if len(shared_behavior) >= 3 and len(shared_component) >= 2:
            rule = "behaviour_profile_plus_component"
        else:
            return IdentityVerdict(
                accepted=False,
                score=score,
                rejection="different_subject",
                shared=shared,
                reasons=[
                    "both reports name specific subjects and none of them match "
                    f"(this report: {sorted(query.structural)[:3]}; "
                    f"candidate: {sorted(candidate.structural)[:3]}); "
                    "a shared error class alone is not the same incident"
                ],
            )
    elif shared_error and (shared_struct or shared_behavior):
        rule = "error_type_plus_second_class"
    elif len(shared_struct) >= 2:
        rule = "two_independent_symbols"
    elif shared_struct and len(shared_behavior) >= 2:
        rule = "symbol_plus_behaviour"
    elif len(shared_behavior) >= 3 and len(shared_component) >= 2:
        rule = "behaviour_profile_plus_component"

    if rule is None:
        detail = (
            f"error={len(shared_error)} structural={len(shared_struct)} "
            f"behavior={len(shared_behavior)} component={len(shared_component)}"
        )
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="insufficient_identity_evidence",
            shared=shared,
            reasons=[
                f"shared features do not establish identity, only topical similarity ({detail})"
            ],
        )

    if score < MIN_IDENTITY_SCORE:
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="identity_score_below_threshold",
            shared=shared,
            rule=rule,
            reasons=[
                f"matched by {rule} but the shared features are too common "
                f"(score {score:.2f} < {MIN_IDENTITY_SCORE})"
            ],
        )

    return IdentityVerdict(
        accepted=True,
        score=score,
        rule=rule,
        shared=shared,
        reasons=[
            f"accepted by {rule}: "
            + "; ".join(f"{name} {values}" for name, values in shared.items() if values)
        ],
    )
