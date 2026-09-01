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

from repo_troubleshooter.fingerprint.error import (
    CAMEL_RE,
    DOTTED_RE,
    DUNDER_RE,
    ERROR_CODE_RE,
    EXCEPTION_RE,
    HYPHEN_MODULE_RE,
    PACKAGE_RE,
    normalize,
)
from repo_troubleshooter.fingerprint.subjects import (
    identifying_path_tail,
    morphology_proves_module,
    syntax_proves_module,
)

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
        r"\bpeer dep\w*\b",
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


# Light stemming: enough to make entries/entry and preloads/preload agree.
def _stem(word: str) -> str:
    lowered = word.lower().strip("`'\".,;:()[]{}")
    if len(lowered) > 4 and lowered.endswith("ies"):
        return lowered[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if len(lowered) > len(suffix) + 3 and lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


# Path-ish tokens are found by splitting, not by a nested-quantifier regex:
# `[\w.@-]+(?:[/\][\w.@-]+)+\.ext` backtracks catastrophically on long logs.
_TOKEN_SPLIT_RE = re.compile(r"[\s,;:()\[\]{}<>\"'`]+")
SOURCE_SUFFIXES = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".rs",
    ".go",
    ".java",
)


def iter_source_paths(text: str) -> list[str]:
    """Tokens that look like a source path with at least one directory."""
    found: list[str] = []
    for token in _TOKEN_SPLIT_RE.split(text):
        cleaned = token.strip("'\"`.,;")
        if ("/" in cleaned or chr(92) in cleaned) and cleaned.lower().endswith(SOURCE_SUFFIXES):
            found.append(cleaned)
    return found


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

    error: set[str] = field(default_factory=set)
    # Strong: packages and source paths. A conflict between two of these is
    # decisive and cannot be offset by a weak subject that happens to overlap.
    subject_strong: set[str] = field(default_factory=set)
    # Weak: module names, admitted only with corpus/syntax/morphology proof.
    subject_weak: set[str] = field(default_factory=set)
    structural: set[str] = field(default_factory=set)
    behavior: set[str] = field(default_factory=set)
    component: set[str] = field(default_factory=set)
    causes: set[str] = field(default_factory=set)

    @property
    def subject(self) -> set[str]:
        """Every named subject, whatever its strength."""
        return self.subject_strong | self.subject_weak

    @property
    def strong(self) -> set[str]:
        """Feature values that can carry identity on their own."""
        return self.error | self.structural | self.subject

    def is_empty(self) -> bool:
        return not (
            self.error or self.subject or self.structural or self.behavior or self.component
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "error": sorted(self.error),
            "subject_strong": sorted(self.subject_strong),
            "subject_weak": sorted(self.subject_weak),
            "structural": sorted(self.structural),
            "behavior": sorted(self.behavior),
            "component": sorted(self.component),
            "causes": sorted(self.causes),
        }

    def as_rows(self) -> list[tuple[str, str]]:
        """(feature_kind, feature_value) pairs, for storage."""
        rows: list[tuple[str, str]] = []
        for kind, values in (
            ("error", self.error),
            ("subject_strong", self.subject_strong),
            ("subject_weak", self.subject_weak),
            ("structural", self.structural),
            ("behavior", self.behavior),
            ("component", self.component),
            ("cause", self.causes),
        ):
            rows.extend((kind, value) for value in sorted(values))
        return rows


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


# Directories every package has; a path tail led by one of these names nothing.
def strong_subjects(text: str) -> set[str]:
    """Packages and source paths: things that exist at a location in the tree."""
    subjects: set[str] = set()

    for match in PACKAGE_RE.finditer(text):
        value = (match.group(1) or match.group(0)).lower()
        subjects.add(value)
        if value.startswith("@") and value.count("/") >= 1:
            scope, _, rest = value.partition("/")
            subjects.add(f"{scope}/{rest.split('/')[0]}")

    for path in iter_source_paths(text.lower()):
        tail = identifying_path_tail(path)
        if tail:
            subjects.add(tail)

    return subjects


def weak_subjects(text: str, known_modules: frozenset[str] = frozenset()) -> set[str]:
    """Module names, admitted only with corpus, syntax or morphology evidence.

    A bare hyphenated phrase is not a subject. `customer-facing` and
    `time-sensitive` reach none of these three proofs and stay out.
    """
    subjects: set[str] = set()
    lowered = text.lower()
    for match in HYPHEN_MODULE_RE.finditer(lowered):
        token = match.group(1)
        if len(token) < 6 or token in COMPONENT_HINTS:
            continue
        if (
            token in known_modules
            or syntax_proves_module(token, text)
            or morphology_proves_module(token)
        ):
            subjects.add(token)
    return subjects


def extract(text: str | None, *, known_modules: frozenset[str] = frozenset()) -> SymptomFeatures:
    """Decompose one piece of symptom text into its feature classes."""
    if not text or not text.strip():
        return SymptomFeatures()

    signature = normalize(text)

    error = {m.lower() for m in EXCEPTION_RE.findall(signature)}
    error |= {m.lower() for m in ERROR_CODE_RE.findall(signature)}

    subject_strong = strong_subjects(signature)
    subject_weak = weak_subjects(signature, known_modules) - subject_strong

    structural: set[str] = set()
    structural |= {m.lower() for m in DUNDER_RE.findall(signature)}
    structural |= {m.lower() for m in DOTTED_RE.findall(signature) if "." in m}
    structural |= {m.lower() for m in CAMEL_RE.findall(signature) if len(m) >= 6}
    structural -= error
    # A symbol is not a subject: `e.indexOf` belongs to whoever called it.
    structural -= subject_strong | subject_weak

    return SymptomFeatures(
        error=error,
        subject_strong=subject_strong,
        subject_weak=subject_weak,
        structural=structural,
        behavior=behavioral_features(signature),
        component=component_features(signature),
        causes=detect_causes(signature),
    )


def merge(*feature_sets: SymptomFeatures) -> SymptomFeatures:
    merged = SymptomFeatures()
    for features in feature_sets:
        merged.error |= features.error
        merged.subject_strong |= features.subject_strong
        merged.subject_weak |= features.subject_weak
        merged.structural |= features.structural
        merged.behavior |= features.behavior
        merged.component |= features.component
        merged.causes |= features.causes
    return merged
