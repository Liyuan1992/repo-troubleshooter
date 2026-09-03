"""Symptom features, split by what they can prove.

A single overall similarity score cannot tell "same problem" from "same words".
So a symptom is decomposed into independent feature classes, and the identity
gate later requires agreement across several of them:

``error``
    Exception types and error codes. Strong identity.
``subject``
    *What the report is about*: a package (`@scope/name`), a module id
    (`theme-parser`), a source path. Two reports naming different subjects
    are about different things, and no amount of shared vocabulary changes
    that - which is why subjects are kept apart from the symbols below.
``structural``
    Symbols: `__GLOBALS__`, dotted calls, camelCase identifiers, bare
    filenames. Strong evidence, but a symbol is shared by everything that
    calls it: `e.indexOf` says nothing about which package failed.
``behavior``
    What the software actually did, normalised from prose: something was empty,
    absent, or never happened. This is what survives paraphrasing - a user who
    writes "the boot graph has no entries" and one who pastes
    "``__DSH_BOOT__`` has zero entries" are describing the same behaviour.
``component``
    Which part of the system is involved.
``environment``
    Runtime and OS. Deliberately weak: it can *veto* a match but never prove one,
    because everybody on Windows shares Windows.
``cause``
    A named, well-understood failure mechanism the reporter stated outright
    (CSP, YAML parse, DNS, auth, disk, port, TLS, permission...). When the
    reporter tells us *why* it failed, a candidate that failed for another
    reason is a different incident no matter how alike the surface looks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from repo_troubleshooter.fingerprint import subjects as subjects_mod
from repo_troubleshooter.fingerprint.error import (
    CAMEL_RE,
    DOTTED_RE,
    DUNDER_RE,
    ERROR_CODE_RE,
    EXCEPTION_RE,
    normalize,
)
from repo_troubleshooter.fingerprint.subjects import classify

# --- cause taxonomy ---------------------------------------------------------
#
# Generic, widely known failure mechanisms. Each entry needs a signal that is
# specific to that mechanism, not a word that merely co-occurs with it.

CAUSE_SIGNALS: dict[str, tuple[str, ...]] = {
    "content_security_policy": (
        r"\bcontent[- ]security[- ]policy\b",
        r"\bcsp\b.*\b(block|refus|violat)",
        r"\brefused to (load|execute|connect)\b",
        r"\bviolates the following .*directive\b",
        r"\b(script|style|connect|img|font)-src\b",
    ),
    "yaml_parse": (
        r"\bduplicate key\b",
        r"\bmapping values are not allowed\b",
        r"\bcould not find expected\b",
        r"\byaml(?:\.\w+)?\s*(parse|syntax|scanner|composer)\w*\s*error\b",
        r"\bwhile (parsing|scanning) a\b",
    ),
    "dns_resolution": (
        r"\bgetaddrinfo\b",
        r"\bEAI_AGAIN\b",
        r"\bENOTFOUND\b",
        r"\bname resolution\b",
        r"\bdns\b.*\b(fail|error|timeout)",
    ),
    "authentication": (
        r"\b401\b",
        r"\b403\b",
        r"\bunauthorized\b",
        r"\bforbidden\b",
        r"\binvalid_grant\b",
        r"\baccess denied\b",
        r"\bauthentication fail",
        r"\bbad credentials\b",
    ),
    "disk_space": (
        r"\bENOSPC\b",
        r"\bno space left\b",
        r"\bdisk (is )?full\b",
        r"\bquota exceeded\b",
    ),
    "port_binding": (
        r"\bEADDRINUSE\b",
        r"\baddress already in use\b",
        r"\bport is already allocated\b",
    ),
    "tls_certificate": (
        r"\bunable to get local issuer\b",
        r"\bself[- ]signed certificate\b",
        r"\bcertificate (verify|validation) fail",
        r"\bSSL certificate problem\b",
        r"\bCERT_\w+\b",
    ),
    "permission": (
        r"\bEACCES\b",
        r"\bEPERM\b",
        r"\bpermission denied\b",
        r"\boperation not permitted\b",
    ),
    "network_connect": (
        r"\bECONNREFUSED\b",
        r"\bETIMEDOUT\b",
        r"\bECONNRESET\b",
        r"\bconnection refused\b",
        r"\bconnection timed out\b",
    ),
    "out_of_memory": (
        r"\bheap out of memory\b",
        r"\bOOM\b",
        r"\bENOMEM\b",
        r"\ballocation failed\b",
    ),
    "module_resolution": (
        r"\bMODULE_NOT_FOUND\b",
        r"\bERR_MODULE_NOT_FOUND\b",
        r"\bcannot find module\b",
        r"\bfailed to resolve (module|import|specifier)\b",
        r"\bresolveSync\b",
        r"\bERR_INVALID_ARG_TYPE\b",
    ),
    "database": (
        r"\brelation \"\w+\" does not exist\b",
        r"\bdeadlock detected\b",
        r"\bREADONLY\b.*\breplica\b",
        r"\b(postgres|postgresql|mysql|sqlite|redis)\b.*\b(error|fail|refus)",
    ),
    "version_conflict": (
        # A peer dependency is a conflict only when something says it conflicts.
        # "peer dependency @scope/lib is healthy" states that nothing is wrong.
        r"\bpeer\s+dep\w*\b[^.\n]{0,60}?\b(?:conflict\w*|mismatch\w*|unmet|unresolved"
        r"|incompatible|invalid|missing|fail\w*|could\s*not\s+\w+|cannot\s+\w+)\b",
        r"\b(?:conflict\w*|mismatch\w*|unmet|unresolved|incompatible)\b[^.\n]{0,60}?"
        r"\bpeer\s+dep\w*\b",
        r"\bERESOLVE\b",
        r"\bincompatible version\b",
        r"\bversion mismatch\b",
    ),
}

_COMPILED_CAUSES = {
    name: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
    for name, patterns in CAUSE_SIGNALS.items()
}

# --- behaviour extraction ---------------------------------------------------
#
# What the software did, stated in a way that survives rewording. We look for an
# absence/failure marker and the thing it applies to.

ABSENCE_MARKERS = (
    r"no",
    r"zero",
    r"none",
    r"not",
    r"never",
    r"empty",
    r"missing",
    r"without",
    r"fails? to",
    r"failed to",
    r"did ?n[o']?t",
    r"does ?n[o']?t",
    r"unable to",
    r"cannot",
    r"can ?not",
    r"blank",
    r"nothing",
)
_ABSENCE_RE = re.compile(
    r"\b(?:" + "|".join(ABSENCE_MARKERS) + r")\b[\s,]*((?:[\w@./`_-]+[\s]+){0,4}[\w@./`_-]+)",
    re.IGNORECASE,
)

# Words that never carry behaviour on their own.
_BEHAVIOUR_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "any",
        "all",
        "some",
        "more",
        "most",
        "very",
        "just",
        "only",
        "also",
        "even",
        "still",
        "yet",
        "when",
        "while",
        "after",
        "before",
        "then",
        "than",
        "so",
        "if",
        "as",
        "my",
        "our",
        "your",
        "their",
        "his",
        "her",
        "i",
        "we",
        "you",
        "they",
        "he",
        "she",
        "get",
        "gets",
        "got",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "will",
        "would",
        "should",
        "may",
        "might",
        "must",
        "up",
        "out",
        "off",
        "over",
        "under",
        "again",
        "back",
        "one",
        "two",
        "new",
        "old",
        "same",
        "other",
        "another",
        "issue",
        "problem",
        "error",
        "errors",
        "bug",
        "reason",
        "way",
        "thing",
        "case",
    }
)


COMPONENT_HINTS = (
    "loader",
    "client",
    "server",
    "host",
    "web",
    "cli",
    "plugin",
    "session",
    "agent",
    "sandbox",
    "gateway",
    "registry",
    "runtime",
    "bundle",
    "boot",
    "browser",
    "module",
    "manifest",
    "config",
    "preset",
    "persistence",
    "proxy",
    "terminal",
    "scheduler",
)


@dataclass
class SymptomFeatures:
    """One symptom, decomposed. Every set is lowercase and normalised."""

    # Subjects, by role. A package conflict is decisive; a path conflict vetoes
    # only when the packages do not already agree; dependencies and modules
    # weaken a match without refusing it; builtins do neither, because every
    # Node program touches `node:path`.
    # Packages the report says *failed*. Only these can establish or refuse
    # package identity.
    subject_packages: set[str] = field(default_factory=set)
    # Packages the report says are merely *used* - `depends on`, `imports`,
    # `peer dependency`. Scoped or not: being scoped proves nothing.
    subject_dependencies: set[str] = field(default_factory=set)
    # Packages the report says are fine: `X is healthy`, `X does not crash`.
    # A package can be here *and* in `subject_dependencies`: "a healthy
    # dependency" states two independent facts, and collapsing them into one
    # role is what let an explicitly cleared package still authorise an upgrade.
    subject_confirmed_non_primary: set[str] = field(default_factory=set)
    # Packages the report contradicts itself about: `X is healthy but crashes`.
    # Stronger than unknown, and never able to authorise an action.
    subject_conflicted: set[str] = field(default_factory=set)
    # Packages named where the role could not be determined. Not harmless: the
    # identity gate treats an unrelated one as a reason to refuse.
    subject_unresolved: set[str] = field(default_factory=set)
    subject_paths: set[str] = field(default_factory=set)
    subject_builtins: set[str] = field(default_factory=set)
    subject_modules: set[str] = field(default_factory=set)
    # Feature values this report evidences *only* inside a fenced block. A
    # quotation can say what a machine printed - so these still find candidates
    # in stage one - but it cannot say that this reporter is having that
    # problem, so they cannot carry identity on their own in stage two.
    quoted_only: set[str] = field(default_factory=set)
    # Packages and condition claims found inside a fence. Recorded rather than
    # deleted: dropping them silently left no trace of material that had been
    # read and thrown away.
    quoted_packages: set[str] = field(default_factory=set)
    quoted_claims: list[dict[str, Any]] = field(default_factory=list)
    # The spans and cues behind the roles above, for the reproduction trace.
    package_mentions: list[dict[str, Any]] = field(default_factory=list)
    # Condition claims this report makes that could not be attached to anything:
    # `We use @a and @b. It crashes.` - something is broken and we cannot say
    # what. The gate refuses to act while one of these is outstanding.
    unresolved_state_assertions: list[dict[str, Any]] = field(default_factory=list)
    # Claims we could see but could not read: `It malfunctions`. Risk, not
    # silence - an unfamiliar verb must not read as "nothing was said".
    uninterpreted_state_assertions: list[dict[str, Any]] = field(default_factory=list)
    # The subset whose subject points straight at a package (`It ...`). These
    # block that package whatever else the report got right.
    pointed_unread_assertions: list[dict[str, Any]] = field(default_factory=list)
    error: set[str] = field(default_factory=set)
    structural: set[str] = field(default_factory=set)
    behavior: set[str] = field(default_factory=set)
    component: set[str] = field(default_factory=set)
    causes: set[str] = field(default_factory=set)

    @property
    def subject(self) -> set[str]:
        """Every named subject, whatever its role."""
        return (
            self.subject_packages
            | self.subject_paths
            | self.subject_dependencies
            | self.subject_confirmed_non_primary
            | self.subject_conflicted
            | self.subject_unresolved
            | self.subject_builtins
            | self.subject_modules
        )

    @property
    def identifying_subjects(self) -> set[str]:
        """Roles that may establish identity - builtins excluded on purpose."""
        return self.subject_packages | self.subject_paths | self.subject_modules

    @property
    def strong(self) -> set[str]:
        """Feature values that can carry identity on their own."""
        return self.error | self.structural | self.identifying_subjects

    def is_empty(self) -> bool:
        return not (
            self.error or self.subject or self.structural or self.behavior or self.component
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "subject_packages": sorted(self.subject_packages),
            "subject_dependencies": sorted(self.subject_dependencies),
            "subject_confirmed_non_primary": sorted(self.subject_confirmed_non_primary),
            "subject_conflicted": sorted(self.subject_conflicted),
            "subject_unresolved": sorted(self.subject_unresolved),
            "package_mentions": self.package_mentions,
            "unresolved_state_assertions": self.unresolved_state_assertions,
            "uninterpreted_state_assertions": self.uninterpreted_state_assertions,
            "pointed_unread_assertions": self.pointed_unread_assertions,
            "subject_paths": sorted(self.subject_paths),
            "subject_builtins": sorted(self.subject_builtins),
            "subject_modules": sorted(self.subject_modules),
            "error": sorted(self.error),
            "structural": sorted(self.structural),
            "behavior": sorted(self.behavior),
            "component": sorted(self.component),
            "causes": sorted(self.causes),
        }

    def as_rows(self) -> list[tuple[str, str]]:
        """(feature_kind, feature_value) pairs, for storage."""
        rows: list[tuple[str, str]] = []
        for kind, values in (
            ("subject_package", self.subject_packages),
            ("subject_dependency", self.subject_dependencies),
            ("subject_confirmed_non_primary", self.subject_confirmed_non_primary),
            ("subject_conflicted", self.subject_conflicted),
            ("subject_unresolved", self.subject_unresolved),
            ("subject_path", self.subject_paths),
            ("subject_builtin", self.subject_builtins),
            ("subject_module", self.subject_modules),
            ("error", self.error),
            ("structural", self.structural),
            ("behavior", self.behavior),
            ("component", self.component),
            ("cause", self.causes),
        ):
            rows.extend((kind, value) for value in sorted(values))
        return rows


# Light stemming: enough to make entries/entry and preloads/preload agree.
def _stem(word: str) -> str:
    lowered = word.lower().strip("`'\".,;:()[]{}")
    if len(lowered) > 4 and lowered.endswith("ies"):
        return lowered[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if len(lowered) > len(suffix) + 3 and lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def detect_causes(text: str) -> set[str]:
    """Named failure mechanisms the text states outright."""
    found: set[str] = set()
    for name, patterns in _COMPILED_CAUSES.items():
        if any(pattern.search(text) for pattern in patterns):
            found.add(name)
    return found


def behavioral_features(text: str) -> set[str]:
    """Canonical "X was absent / did not happen" facts, robust to rewording."""
    features: set[str] = set()
    for match in _ABSENCE_RE.finditer(text):
        window = match.group(1)
        for word in re.split(r"[\s,]+", window):
            stem = _stem(word)
            if not stem or stem in _BEHAVIOUR_STOP or len(stem) < 3:
                continue
            if stem.isdigit():
                continue
            features.add(f"absent:{stem}")
    return features


def component_features(text: str) -> set[str]:
    """Generic areas of the system. Weak on their own - everyone has a client."""
    lowered = text.lower()
    return {hint for hint in COMPONENT_HINTS if re.search(rf"\b{hint}s?\b", lowered)}


def extract(text: str | None, *, known_modules: frozenset[str] = frozenset()) -> SymptomFeatures:
    """Decompose one piece of symptom text into its feature classes."""
    if not text or not text.strip():
        return SymptomFeatures()

    signature = normalize(text)

    error = {m.lower() for m in EXCEPTION_RE.findall(signature)}
    error |= {m.lower() for m in ERROR_CODE_RE.findall(signature)}

    subjects = classify(signature, known_modules)

    structural: set[str] = set()
    structural |= {m.lower() for m in DUNDER_RE.findall(signature)}
    structural |= {m.lower() for m in DOTTED_RE.findall(signature) if "." in m}
    structural |= {m.lower() for m in CAMEL_RE.findall(signature) if len(m) >= 6}
    structural -= error
    # A symbol is not a subject: `e.indexOf` belongs to whoever called it.
    structural -= subjects.all

    features = SymptomFeatures(
        error=error,
        subject_packages=subjects.primary_packages,
        subject_dependencies=subjects.dependencies,
        subject_confirmed_non_primary=subjects.healthy_packages,
        subject_conflicted=subjects.conflicted_packages,
        subject_unresolved=subjects.unresolved_packages,
        package_mentions=[m.to_json() for m in subjects.package_mentions],
        unresolved_state_assertions=[a.to_json() for a in subjects.unresolved_assertions],
        uninterpreted_state_assertions=[a.to_json() for a in subjects.uninterpreted_assertions],
        pointed_unread_assertions=[a.to_json() for a in subjects.pointed_unread_assertions],
        subject_paths=subjects.paths,
        subject_builtins=subjects.builtins,
        subject_modules=subjects.modules,
        structural=structural,
        behavior=behavioral_features(signature),
        component=component_features(signature),
        causes=detect_causes(signature),
    )
    _record_quotation(features, signature, known_modules)
    return features


def identity_values(features: SymptomFeatures) -> set[str]:
    """Everything that can carry same-incident identity on its own."""
    return (
        features.error
        | features.structural
        | features.subject_paths
        | features.subject_modules
        | features.subject_packages
        | features.subject_dependencies
    )


def _record_quotation(
    features: SymptomFeatures, signature: str, known_modules: frozenset[str]
) -> None:
    """Split this report's evidence into what it states and what it quotes.

    Fenced material is kept, not deleted: its packages and claims are recorded
    as quoted, and the identity values it is the *only* source of are marked so
    the gate can decline to act on them alone.
    """
    spans = subjects_mod.quoted_spans(signature)
    if not spans:
        return
    without = subjects_mod.blank_quoted(signature)
    if without != signature:
        features.quoted_only = identity_values(features) - identity_values(
            extract(without, known_modules=known_modules)
        )
    inside = "\n".join(signature[start:end] for start, end in spans)
    quoted = classify(inside, known_modules)
    features.quoted_packages = set(quoted.all_packages)
    features.quoted_claims = [a.to_json() for a in quoted.state_assertions]


def merge(*feature_sets: SymptomFeatures) -> SymptomFeatures:
    merged = SymptomFeatures()
    for features in feature_sets:
        merged.error |= features.error
        merged.subject_packages |= features.subject_packages
        merged.subject_dependencies |= features.subject_dependencies
        merged.subject_confirmed_non_primary |= features.subject_confirmed_non_primary
        merged.subject_conflicted |= features.subject_conflicted
        merged.subject_unresolved |= features.subject_unresolved
        merged.subject_paths |= features.subject_paths
        merged.quoted_only |= features.quoted_only
        merged.quoted_packages |= features.quoted_packages
        merged.quoted_claims += features.quoted_claims
        merged.package_mentions += features.package_mentions
        merged.unresolved_state_assertions += features.unresolved_state_assertions
        merged.uninterpreted_state_assertions += features.uninterpreted_state_assertions
        merged.pointed_unread_assertions += features.pointed_unread_assertions
        merged.subject_builtins |= features.subject_builtins
        merged.subject_modules |= features.subject_modules
        merged.structural |= features.structural
        merged.behavior |= features.behavior
        merged.component |= features.component
        merged.causes |= features.causes
    return merged
