"""Structured candidate constraints supplied by the caller.

These facts deliberately work in only one direction: every anchor must be
present in the candidate's non-quoted evidence, otherwise the candidate is
rejected.  Passing anchors does not prove same-incident identity and never
grants action authorization.
"""

from __future__ import annotations

from repo_troubleshooter.diagnosis.contract import StructuredAnchor
from repo_troubleshooter.fingerprint.features import SymptomFeatures

_ANCHOR_FIELDS: dict[str, str] = {
    "error": "error",
    "structural": "structural",
    "subject_package": "subject_packages",
    "subject_path": "subject_paths",
    "subject_module": "subject_modules",
}


def mismatches(
    anchors: tuple[StructuredAnchor, ...], candidate: SymptomFeatures
) -> list[StructuredAnchor]:
    """Return constraints not demonstrated by the candidate itself.

    A fenced example or copied vendor ticket may still help stage-one retrieval,
    but it cannot satisfy a user-supplied identity constraint at stage two.
    """
    stated = candidate.unquoted or candidate
    return [
        anchor
        for anchor in anchors
        if anchor.value not in getattr(stated, _ANCHOR_FIELDS[anchor.kind])
    ]


def labels(anchors: tuple[StructuredAnchor, ...]) -> list[str]:
    """Stable public labels for an explanation or trace."""
    return [f"{anchor.kind}:{anchor.value}" for anchor in anchors]
