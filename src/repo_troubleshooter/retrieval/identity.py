"""The `accepted_same_incident` gate.

Retrieval answers "is this worth looking at". This module answers the much
harder question the product is judged on: **is this the same problem?**

Four rules, each learned from a measured failure:

1. **A stated root cause is decisive.** If the reporter says the browser refused
   the script under a Content-Security-Policy directive, a loader bug with a
   similar surface is not their problem, however well the words line up.

2. **Subjects disagree by role, strongest first.** A scoped package is the
   primary subject: two reports about different packages are about different
   things, and a shared `node:path`, a shared dependency or even a shared source
   path cannot buy that off. A path conflict vetoes only when the packages do
   not already agree. Dependencies and module names *weaken* a match rather than
   refusing it, and `node:*` builtins do neither - every Node program touches
   them.

3. **Identity needs agreement across independent feature classes.** One rare
   word, one shared filename or one shared component is a coincidence.

4. **Environment can only veto.** Everyone on Windows shares Windows; sharing it
   proves nothing. Runtime and OS are handled by the applicability gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from repo_troubleshooter.fingerprint.features import SymptomFeatures

# What a shared feature of each class is worth. Builtins are absent on purpose.
CLASS_WEIGHTS = {
    "subject_package": 3.0,
    "subject_path": 2.5,
    "subject_module": 1.5,
    "subject_dependency": 0.75,
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
    # Roles that disagree without refusing the match outright.
    weakened_by: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "score": round(self.score, 2),
            "rule": self.rule,
            "rejection": self.rejection,
            "shared": self.shared,
            "reasons": self.reasons,
            "conflicting_causes": self.conflicting_causes,
            "weakened_by": self.weakened_by,
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


def _named_subjects_disjoint(query: SymptomFeatures, candidate: SymptomFeatures) -> bool:
    """True when both sides name something identifying and nothing connects them.

    Containment counts as a connection: a module `client-modules` and a package
    `@deepseek-ai/dsh-client-modules` are the same thing said two ways.
    """
    left, right = query.identifying_subjects, candidate.identifying_subjects
    if not left or not right:
        return False
    for value in left:
        for other in right:
            if value == other or value in other or other in value:
                return False
    return True


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

    shared_package = query.subject_packages & candidate.subject_packages
    shared_path = query.subject_paths & candidate.subject_paths
    shared_module = query.subject_modules & candidate.subject_modules
    shared_dependency = query.subject_dependencies & candidate.subject_dependencies
    shared_error = query.error & candidate.error
    shared_struct = query.structural & candidate.structural
    shared_behavior = query.behavior & candidate.behavior
    shared_component = query.component & candidate.component

    shared = {
        "subject_package": sorted(shared_package),
        "subject_path": sorted(shared_path),
        "subject_module": sorted(shared_module),
        "subject_dependency": sorted(shared_dependency),
        "error": sorted(shared_error),
        "structural": sorted(shared_struct),
        "behavior": sorted(shared_behavior),
        "component": sorted(shared_component),
    }

    score = (
        _weighted(shared_package, CLASS_WEIGHTS["subject_package"], doc_freq, corpus_objects)
        + _weighted(shared_path, CLASS_WEIGHTS["subject_path"], doc_freq, corpus_objects)
        + _weighted(shared_module, CLASS_WEIGHTS["subject_module"], doc_freq, corpus_objects)
        + _weighted(
            shared_dependency, CLASS_WEIGHTS["subject_dependency"], doc_freq, corpus_objects
        )
        + _weighted(shared_error, CLASS_WEIGHTS["error"], doc_freq, corpus_objects)
        + _weighted(shared_struct, CLASS_WEIGHTS["structural"], doc_freq, corpus_objects)
        + _weighted(shared_behavior, CLASS_WEIGHTS["behavior"], doc_freq, corpus_objects)
        + _weighted(shared_component, CLASS_WEIGHTS["component"], doc_freq, corpus_objects)
    )

    # --- rule 1: an explicitly stated cause decides before anything else ---
    if query.causes:
        if not query.causes & candidate.causes:
            return IdentityVerdict(
                accepted=False,
                score=0.0,
                rejection="different_root_cause",
                shared=shared,
                conflicting_causes=sorted(query.causes),
                reasons=[_different_cause_reason(query.causes, candidate.causes)],
            )

    # --- rule 2a: primary package conflict. Nothing overrides this. --------
    if query.subject_packages and candidate.subject_packages and not shared_package:
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="different_subject",
            shared=shared,
            reasons=[
                "the reports name different packages "
                f"(this report: {sorted(query.subject_packages)[:3]}; "
                f"candidate: {sorted(candidate.subject_packages)[:3]}). "
                "A shared runtime builtin, dependency, source path or symbol does "
                "not make two packages the same subject"
            ],
        )

    # --- rule 2b: source-path conflict, unless the packages already agree --
    if not shared_package and query.subject_paths and candidate.subject_paths and not shared_path:
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="different_subject",
            shared=shared,
            reasons=[
                "the reports point at different source paths "
                f"(this report: {sorted(query.subject_paths)[:3]}; "
                f"candidate: {sorted(candidate.subject_paths)[:3]}), "
                "and no package names them as the same subject"
            ],
        )

    # --- rule 2c: weaker roles cannot refuse, only raise the bar -----------
    weakened_by: list[str] = []
    if query.subject_modules and candidate.subject_modules and not shared_module:
        weakened_by.append("module names disagree")
    elif _named_subjects_disjoint(query, candidate):
        # One side names `theme-parser`, the other names a package and a path,
        # and nothing connects them. Not a veto - the roles differ, so the
        # evidence is weaker rather than contradictory - but a match resting on
        # a generic symbol or exception type is no longer good enough.
        weakened_by.append("neither report names a subject the other names")
    if query.subject_dependencies and candidate.subject_dependencies and not shared_dependency:
        weakened_by.append("referenced dependencies disagree")

    # --- rule 3: which combination of classes is enough to mean "same" -----
    rule: str | None = None
    if shared_package and (shared_error or shared_struct or shared_behavior):
        rule = "primary_package_plus_second_class"
    elif shared_path and (shared_error or shared_struct or shared_behavior):
        rule = "source_path_plus_second_class"
    elif shared_module and (shared_error or shared_struct or len(shared_behavior) >= 2):
        rule = "module_plus_second_class"
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
            f"package={len(shared_package)} path={len(shared_path)} "
            f"module={len(shared_module)} error={len(shared_error)} "
            f"structural={len(shared_struct)} behavior={len(shared_behavior)} "
            f"component={len(shared_component)}"
        )
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="insufficient_identity_evidence",
            shared=shared,
            weakened_by=weakened_by,
            reasons=[
                f"shared features do not establish identity, only topical similarity ({detail})"
            ],
        )

    # A weaker-role disagreement does not refuse the match, but a match resting
    # only on a generic symbol or exception type is no longer good enough.
    SUBJECT_RULES = (
        "primary_package_plus_second_class",
        "source_path_plus_second_class",
        "module_plus_second_class",
    )
    if weakened_by and rule not in (*SUBJECT_RULES, "behaviour_profile_plus_component"):
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="insufficient_identity_evidence",
            shared=shared,
            rule=rule,
            weakened_by=weakened_by,
            reasons=[
                f"{'; '.join(weakened_by)}, so a match resting on {rule} is not enough "
                "to call these the same incident"
            ],
        )

    if score < MIN_IDENTITY_SCORE:
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="identity_score_below_threshold",
            shared=shared,
            rule=rule,
            weakened_by=weakened_by,
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
        weakened_by=weakened_by,
        reasons=[
            f"accepted by {rule}: "
            + "; ".join(f"{name} {values}" for name, values in shared.items() if values)
        ],
    )
