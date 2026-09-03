"""The `accepted_same_incident` gate.

Retrieval answers "is this worth looking at". This module answers the much
harder question the product is judged on: **is this the same problem?**

Four rules, each learned from a measured failure:

1. **A stated root cause is decisive.** If the reporter says the browser refused
   the script under a Content-Security-Policy directive, a loader bug with a
   similar surface is not their problem, however well the words line up.

2. **Subjects disagree by role, strongest first.** A package is *primary* only
   when the report says it failed - being scoped proves nothing, so
   `depends on @scope/x` never makes `@scope/x` the subject. Two reports whose
   primary packages disagree are about different things, and a shared
   `node:path`, a shared dependency, a shared source path or a shared module
   name cannot buy that off. A path conflict vetoes only when the primary
   packages do not already agree. Dependencies, bare mentions and module names
   *weaken* a match rather than refusing it, and `node:*` builtins do neither.

   Two primary packages that belong to the same product - learned from the
   repository's own manifests, never from a hardcoded name - are not a conflict:
   one ships inside the other.

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
from repo_troubleshooter.fingerprint.subjects import (
    SOURCE_EXPLICIT_PACKAGE,
    SOURCE_RESOLVED_ANAPHOR,
)
from repo_troubleshooter.versions.packages import PackageFamily

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


#: The shared feature classes that can establish same-incident identity on
#: their own. `behavior` and `component` describe what kind of problem this is,
#: not that it is the same one, and the gate already refuses a match resting on
#: them alone.
IDENTITY_CLASSES = (
    "subject_package",
    "related_packages",
    "subject_path",
    "subject_module",
    "subject_dependency",
    "error",
    "structural",
)


def evaluate(
    query: SymptomFeatures,
    candidate: SymptomFeatures,
    *,
    doc_freq: dict[str, int] | None = None,
    corpus_objects: int = 0,
    package_family: PackageFamily | None = None,
) -> IdentityVerdict:
    """Decide whether a retrieved candidate is the *same incident* as the query."""
    doc_freq = doc_freq or {}
    family = package_family or PackageFamily()

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

    candidate_any_package = (
        candidate.subject_packages
        | candidate.subject_dependencies
        | candidate.subject_confirmed_non_primary
        | candidate.subject_conflicted
        | candidate.subject_unresolved
    )

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

    # --- rule 0b: the report clears every package it names --------------------
    #
    # When a report names packages, says all of them are fine, and never names a
    # culprit, whatever failed is something it did not name. No rule may act on
    # that - not a shared package, not a shared path, not a shared module. The
    # shared source path is the tempting one: it is genuinely the same file, but
    # the report already told us the package that owns it is healthy.
    #
    # A report that names no package at all (a pasted log) is deliberately not
    # covered: its rare symbols still carry identity, which is how
    # snippet-only incidents are matched.
    # `subject_confirmed_non_primary` now holds every package the report calls
    # healthy, including ones it also calls dependencies. Reading the state fact
    # rather than the final role is the point: "a healthy dependency" used to
    # collapse to "dependency" and slip past this check.
    if (
        query.subject_confirmed_non_primary
        and not query.subject_packages
        and not query.subject_unresolved
        and not query.subject_conflicted
    ):
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="exculpated_subject",
            shared=shared,
            reasons=[
                "this report clears every package it names "
                f"({sorted(query.subject_confirmed_non_primary)[:3]}) and never says what "
                "failed, so nothing here identifies an incident - not a shared path, module "
                "or symbol"
            ],
        )

    # --- rule 0b-bis: claims we could not finish reading ----------------------
    #
    # Two kinds, dangerous in different ways:
    #
    #   targeted   - we know which package it is about and cannot read it:
    #                "We import @x. It malfunctions." Whatever @x's condition
    #                is, this report did not say it in words we understand, so
    #                no action about @x is authorised.
    #   untargeted - we cannot say what it is about: a log line, a quoted
    #                fragment. Harmless alone; a problem only when the report
    #                also names a package the candidate has never heard of,
    #                because the claim might be about that one.
    #
    # An unbound *health* claim never blocks: it cannot cause a wrong upgrade.
    unread = [a for a in query.unresolved_state_assertions if a.get("state") != "healthy"]
    unread += list(query.uninterpreted_state_assertions)
    # Pointed claims - `It is operational` - name the package by pointing at
    # it, and block it unconditionally: a shared primary earlier in the report
    # does not settle what a later sentence was trying to say. Weak ones,
    # whose subject is ordinary prose that merely follows a mention
    # (`dsh web starts`), only count when nothing else establishes identity.
    pointed = [a for a in query.pointed_unread_assertions if a.get("package")]
    # A pointed claim whose target we could not resolve - `Said package is
    # operational` in a report that names no package we recognise - is still a
    # claim about a package. Which one is unknown, so no package is safe to act
    # on, and identity from a path or symbol cannot settle it either.
    pointed_unresolved = [a for a in query.pointed_unread_assertions if not a.get("package")]
    # Health is not the harmless direction. "An unbound health claim cannot
    # cause a wrong upgrade" holds only for a claim about nothing in
    # particular; one whose subject points at a package is retracting a
    # failure, and dropping it let `@x crashes! Said package is healthy.` act
    # on the failure it had just taken back.
    pointed_unresolved += [
        a
        for a in query.unresolved_state_assertions
        if a.get("state") == "healthy"
        and a.get("source") in {SOURCE_EXPLICIT_PACKAGE, SOURCE_RESOLVED_ANAPHOR}
    ]
    weak_targeted = [
        a for a in unread if a.get("package") and a.get("binding") == "uninterpreted_weak"
    ]
    untargeted = [a for a in unread if not a.get("package")]

    named_packages = (
        query.subject_packages
        | query.subject_dependencies
        | query.subject_confirmed_non_primary
        | query.subject_conflicted
        | query.subject_unresolved
    )
    unaccounted = {
        name
        for name in named_packages
        if name not in candidate_any_package
        and not family.any_related({name}, candidate_any_package)
    }
    has_package_identity = bool(shared_package or shared.get("related_packages"))

    # Unconditional. A shared primary package does not license us to ignore a
    # claim about that same package we could not read: `@x crashes! It is
    # operational.` is a report contradicting itself in words we do not
    # understand, and the earlier sentence agreeing with a candidate does not
    # settle it. Requiring "no package identity" here was exactly the bypass.
    if pointed:
        blamed = sorted({str(a["package"]) for a in pointed})[:3]
        cues = [a.get("cue", "") for a in pointed][:2]
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="unread_claim_about_a_named_package",
            shared=shared,
            reasons=[
                f"this report says something about {blamed} that could not be read ({cues}), "
                "so its condition was never established and a shared path, module or symbol "
                "cannot authorise an action"
            ],
        )

    if (weak_targeted or untargeted) and unaccounted and not has_package_identity:
        cues = [a.get("cue", "") for a in untargeted][:2]
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="unbound_state_assertion",
            shared=shared,
            reasons=[
                f"this report claims a condition ({cues}) without saying what it is about, "
                f"while naming {sorted(unaccounted)[:3]}, which the candidate never mentions"
            ],
        )

    # --- rule 0d: a used package whose condition was never established -------
    #
    # The conservative rule, chosen over extending the predicate vocabulary. If
    # the report names a package, never says whether it is failing or fine, and
    # the candidate does not name it either, then a shared path, module or
    # symbol cannot say this is the same incident: those describe where code
    # lives, not what broke. Only agreement on a named package carries it.
    #
    # It holds for every phrasing, including the ones nobody has written yet.
    unestablished = {
        name
        for name in (query.subject_dependencies | query.subject_unresolved)
        if name not in query.subject_confirmed_non_primary
        and name not in candidate_any_package
        and not family.any_related({name}, candidate_any_package)
    }
    if unestablished and not (shared_package or shared.get("related_packages")):
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="unestablished_subject",
            shared=shared,
            reasons=[
                f"this report names {sorted(unestablished)[:3]} without establishing whether "
                "it failed or is fine, and the candidate never mentions it; a shared path, "
                "module or symbol does not make this the same incident"
            ],
        )

    # --- rule 0c: the report contradicts itself about a package ---------------
    #
    # `X is healthy but crashes` says something is wrong *and* that it is not.
    # That cannot authorise an action even when the candidate names the very
    # same package.
    if query.subject_conflicted:
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="conflicted_subject",
            shared=shared,
            reasons=[
                "this report says contradictory things about "
                f"{sorted(query.subject_conflicted)[:3]}, so it cannot establish what failed"
            ],
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

    # --- rule 1b: a claim about some package, in words we could not read ---
    #
    # Both refusals are correct; this one runs after the stated cause because a
    # report that names its own cause has told us something firmer than "a
    # sentence here could not be read", and the reason a user sees should be
    # the firmer one.
    if pointed_unresolved:
        cues = [a.get("cue", "") for a in pointed_unresolved][:2]
        subjects_seen = [a.get("subject", "") for a in pointed_unresolved][:2]
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="unread_claim_about_an_unnamed_package",
            shared=shared,
            reasons=[
                f"this report states a condition of some package ({subjects_seen}) in words "
                f"that could not be read ({cues}), and never says which package, so no "
                "action about any package is authorised"
            ],
        )

    # --- rule 2a: primary package conflict. Nothing overrides this. --------
    #
    # `subject_packages` holds only packages the report says *failed*. A package
    # the report merely uses lives in `subject_dependencies` and is never
    # consulted here, so a shared dependency cannot cancel this conflict.
    if query.subject_packages and candidate.subject_packages and not shared_package:
        related = family.any_related(query.subject_packages, candidate.subject_packages)
        if not related:
            return IdentityVerdict(
                accepted=False,
                score=score,
                rejection="different_subject",
                shared=shared,
                reasons=[
                    "the reports blame different packages "
                    f"(this report: {sorted(query.subject_packages)[:3]}; "
                    f"candidate: {sorted(candidate.subject_packages)[:3]}). "
                    "A shared runtime builtin, dependency, source path, module name "
                    "or symbol does not make two packages the same subject"
                ],
            )
        related_packages = sorted({f"{a} ~ {b}" for a, b in related})
        shared["related_packages"] = related_packages

    # --- rule 2a-bis: the query blames a package this candidate never names --
    #
    # A shared source path is not enough when the report points at a package the
    # candidate does not discuss at all. `@nebula/theme-engine is not working`
    # plus a familiar stack path is a report about Nebula, not about the thread
    # that blames a different package.
    if (
        query.subject_packages
        and candidate_any_package
        and not (query.subject_packages & candidate_any_package)
        and not family.any_related(query.subject_packages, candidate_any_package)
    ):
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="different_subject",
            shared=shared,
            reasons=[
                "this report blames "
                f"{sorted(query.subject_packages)[:3]}, which the candidate never names "
                f"(it discusses {sorted(candidate_any_package)[:3]}); "
                "a shared source path or symbol does not make them the same subject"
            ],
        )

    # --- rule 2a-ter: an unresolved package the candidate does not know -----
    #
    # The safety default. Every previous round of this gate leaked through a
    # phrasing the cue vocabulary did not recognise: the package became a
    # neutral mention, and a neutral mention cannot refuse anything. So when the
    # query names a package whose role we could not determine, and the candidate
    # never names it, the only things left connecting them are a dependency, a
    # path or a symbol - none of which say *what* broke. That is not enough to
    # call it the same incident, and it is certainly not enough to tell someone
    # to upgrade.
    unresolved_and_unknown = {
        name
        for name in query.subject_unresolved
        if name not in candidate_any_package
        and not family.any_related({name}, candidate_any_package)
    }
    identity_from_subject = bool(shared_package) or bool(shared.get("related_packages"))
    if unresolved_and_unknown and not identity_from_subject:
        return IdentityVerdict(
            accepted=False,
            score=score,
            rejection="unresolved_subject",
            shared=shared,
            reasons=[
                f"this report names {sorted(unresolved_and_unknown)[:3]} without saying what "
                "role it plays, and the candidate never mentions it; the only links are a "
                "dependency, a path or a symbol, which do not establish what failed"
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
    if (
        query.subject_unresolved
        and candidate.subject_unresolved
        and not (query.subject_unresolved & candidate.subject_unresolved)
    ):
        weakened_by.append("packages named without a determinable role disagree")

    # --- rule 3: which combination of classes is enough to mean "same" -----
    rule: str | None = None
    related_packages = shared.get("related_packages") or []
    if shared_package and (shared_error or shared_struct or shared_behavior):
        rule = "primary_package_plus_second_class"
    elif related_packages and (shared_error or shared_struct or shared_behavior):
        # Same product, different package inside it: a real relation, but a
        # weaker one than naming the same package.
        rule = "related_package_plus_second_class"
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

    # --- the quotation bridge ------------------------------------------------
    #
    # A fenced block is material the reporter is showing: a documentation
    # example, an old ticket, somebody else's output. Its paths and symbols are
    # real strings and may find candidates - stage one - but nothing inside it
    # says that *this* reporter has that problem. So identity has to rest on at
    # least one thing stated outside the quotation. Without that bridge the
    # report stops at retrieved_candidate, which is exactly what it is.
    quoted_only = query.quoted_only
    if quoted_only:
        bridging = {
            value
            for name in IDENTITY_CLASSES
            for value in shared.get(name, ())
            if value not in quoted_only
        }
        if not bridging:
            return IdentityVerdict(
                accepted=False,
                score=score,
                rejection="quoted_evidence_only",
                shared=shared,
                reasons=[
                    "everything this report and the candidate share "
                    f"({sorted(quoted_only)[:3]}) appears only inside quoted material, so "
                    "nothing the reporter states themselves identifies this as the same "
                    "incident"
                ],
            )

    # A weaker-role disagreement does not refuse the match, but a match resting
    # only on a generic symbol or exception type is no longer good enough.
    SUBJECT_RULES = (
        "primary_package_plus_second_class",
        "related_package_plus_second_class",
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
