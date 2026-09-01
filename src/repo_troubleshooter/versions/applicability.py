"""Applicability gate.

Retrieval finds candidates that are *textually* similar. This module decides
whether a candidate can possibly apply to the user's actual environment, and it
runs before any action is proposed.

Two rules it must never break:

* Unknown is not false. An unparseable version yields ``unresolved_version``,
  never a silent "does not apply" and never an "already contains".
* Only an explicit, sourced constraint can produce a hard contradiction. A
  passing mention in one user's report is an observation, not a bound.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from repo_troubleshooter.versions import semver


class Applicability(StrEnum):
    DIRECT_MATCH = "direct_match"
    COMPATIBLE_UNKNOWN = "compatible_unknown"
    POSSIBLE_CONTRADICTION = "possible_contradiction"
    HARD_CONTRADICTION = "hard_contradiction"
    UNRESOLVED_VERSION = "unresolved_version"


# --- constraint extraction ---------------------------------------------------

_RUNTIMES = "node|nodejs|node\\.js|python|deno|bun"
_V = r"\d+(?:\.\d+){0,2}"

RANGE_RE = re.compile(
    rf"\b({_RUNTIMES})\s*(?:v|version)?\s*({_V})\s*(?:-|–|—|~|to|through)\s*({_V})", re.IGNORECASE
)
MIN_RE = re.compile(rf"\b({_RUNTIMES})\s*(?:>=|≥|at least)\s*({_V})", re.IGNORECASE)
MAX_RE = re.compile(rf"\b({_RUNTIMES})\s*(?:<=|<|≤|below|before)\s*({_V})", re.IGNORECASE)
POINT_RE = re.compile(rf"\b({_RUNTIMES})\s*(?:v|version)?\s*({_V})\b", re.IGNORECASE)
OS_RE = re.compile(r"\b(windows|win32|linux|macos|darwin|ubuntu|wsl)\b", re.IGNORECASE)

_OS_ALIASES = {
    "win32": "windows",
    "darwin": "macos",
    "ubuntu": "linux",
    "wsl": "linux",
}


def _canonical_runtime(name: str) -> str:
    lowered = name.lower().replace(".js", "").replace("js", "") or name.lower()
    return "node" if lowered.startswith("node") else lowered


def _canonical_os(name: str) -> str:
    lowered = name.lower()
    return _OS_ALIASES.get(lowered, lowered)


@dataclass
class RuntimeConstraint:
    runtime: str
    min_inclusive: str | None = None
    max_inclusive: str | None = None
    points: tuple[str, ...] = ()
    source: str = "text_explicit"
    evidence_id: str | None = None

    def describe(self) -> str:
        if self.min_inclusive and self.max_inclusive:
            return f"{self.runtime} {self.min_inclusive}-{self.max_inclusive}"
        if self.min_inclusive:
            return f"{self.runtime} >= {self.min_inclusive}"
        if self.max_inclusive:
            return f"{self.runtime} <= {self.max_inclusive}"
        if self.points:
            return f"{self.runtime} {', '.join(self.points)}"
        return self.runtime

    def covers(self, version: str | None) -> bool | None:
        """True/False when decidable, None when the version cannot be parsed."""
        target = semver.parse(version)
        if target is None:
            return None
        if self.min_inclusive or self.max_inclusive:
            low = semver.parse(self.min_inclusive) if self.min_inclusive else None
            high = semver.parse(self.max_inclusive) if self.max_inclusive else None
            if self.min_inclusive and low is None:
                return None
            if self.max_inclusive and high is None:
                return None
            if low is not None and target < low:
                return False
            if high is not None and target > high:
                # 24.0-24.11.1 must still cover 24.11 stated as "24.11"
                if not str(target).startswith(str(high)):
                    return False
            return True
        if self.points:
            for point in self.points:
                parsed = semver.parse(point)
                if parsed is None:
                    continue
                if parsed == target:
                    return True
                # "Node 24.11" covers 24.11.1
                if str(target).startswith(f"{parsed}."):
                    return True
            return False
        return None


@dataclass
class ExtractedConstraints:
    runtimes: list[RuntimeConstraint] = field(default_factory=list)
    operating_systems: set[str] = field(default_factory=set)

    def to_json(self) -> dict[str, Any]:
        return {
            "runtimes": [
                {
                    "runtime": c.runtime,
                    "min_inclusive": c.min_inclusive,
                    "max_inclusive": c.max_inclusive,
                    "points": list(c.points),
                    "source": c.source,
                    "evidence_id": c.evidence_id,
                }
                for c in self.runtimes
            ],
            "operating_systems": sorted(self.operating_systems),
        }


def extract_constraints(
    text: str | None, *, source: str = "text_explicit", evidence_id: str | None = None
) -> ExtractedConstraints:
    """Pull runtime/OS bounds out of evidence text. Ranges win over point mentions."""
    result = ExtractedConstraints()
    if not text:
        return result

    consumed: set[tuple[str, str]] = set()

    for runtime, low, high in RANGE_RE.findall(text):
        canonical = _canonical_runtime(runtime)
        result.runtimes.append(
            RuntimeConstraint(canonical, low, high, source=source, evidence_id=evidence_id)
        )
        consumed.add((canonical, low))
        consumed.add((canonical, high))

    for runtime, low in MIN_RE.findall(text):
        canonical = _canonical_runtime(runtime)
        result.runtimes.append(
            RuntimeConstraint(canonical, min_inclusive=low, source=source, evidence_id=evidence_id)
        )
        consumed.add((canonical, low))

    for runtime, high in MAX_RE.findall(text):
        canonical = _canonical_runtime(runtime)
        result.runtimes.append(
            RuntimeConstraint(canonical, max_inclusive=high, source=source, evidence_id=evidence_id)
        )
        consumed.add((canonical, high))

    points: dict[str, list[str]] = {}
    for runtime, version in POINT_RE.findall(text):
        canonical = _canonical_runtime(runtime)
        if (canonical, version) in consumed:
            continue
        points.setdefault(canonical, []).append(version)
    for canonical, versions in points.items():
        result.runtimes.append(
            RuntimeConstraint(
                canonical,
                points=tuple(dict.fromkeys(versions)),
                source=source,
                evidence_id=evidence_id,
            )
        )

    for os_name in OS_RE.findall(text):
        result.operating_systems.add(_canonical_os(os_name))

    return result


# --- verdict -----------------------------------------------------------------


@dataclass
class ApplicabilityVerdict:
    status: Applicability
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def blocks_action(self) -> bool:
        return self.status in (Applicability.HARD_CONTRADICTION, Applicability.UNRESOLVED_VERSION)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "reasons": self.reasons,
            "conflicts": self.conflicts,
            "evidence_ids": self.evidence_ids,
        }


def evaluate(
    *,
    core_version: str | None,
    runtime_name: str | None,
    runtime_version: str | None,
    os_name: str | None,
    constraints: ExtractedConstraints,
) -> ApplicabilityVerdict:
    """Decide whether an incident can apply to this environment."""
    reasons: list[str] = []
    conflicts: list[str] = []
    evidence_ids: list[str] = []
    status = Applicability.COMPATIBLE_UNKNOWN

    if core_version and semver.parse(core_version) is None:
        reasons.append(
            f"core version {core_version!r} is not a comparable version; "
            "release ordering cannot be decided"
        )
        return ApplicabilityVerdict(
            Applicability.UNRESOLVED_VERSION, reasons, conflicts, evidence_ids
        )

    matched_runtime = False
    for constraint in constraints.runtimes:
        if runtime_name and constraint.runtime != runtime_name:
            continue
        covered = constraint.covers(runtime_version)
        if constraint.evidence_id:
            evidence_ids.append(constraint.evidence_id)
        if covered is None:
            reasons.append(
                f"runtime {runtime_name or '?'} {runtime_version or '?'} cannot be compared "
                f"with stated constraint {constraint.describe()}"
            )
            status = _weaken(status, Applicability.UNRESOLVED_VERSION)
            continue
        if covered:
            matched_runtime = True
            reasons.append(
                f"runtime {runtime_name} {runtime_version} is inside the stated "
                f"constraint {constraint.describe()}"
            )
        else:
            message = (
                f"runtime {runtime_name} {runtime_version} is outside the stated "
                f"constraint {constraint.describe()}"
            )
            conflicts.append(message)
            reasons.append(message)
            status = _weaken(
                status,
                Applicability.HARD_CONTRADICTION
                if constraint.source == "explicit"
                else Applicability.POSSIBLE_CONTRADICTION,
            )

    if os_name and constraints.operating_systems:
        if _canonical_os(os_name) in constraints.operating_systems:
            reasons.append(f"OS {os_name} matches the reported environment")
        else:
            reasons.append(
                f"OS {os_name} is not among the reported ones "
                f"({', '.join(sorted(constraints.operating_systems))})"
            )
            status = _weaken(status, Applicability.POSSIBLE_CONTRADICTION)

    if status == Applicability.COMPATIBLE_UNKNOWN and matched_runtime:
        status = Applicability.DIRECT_MATCH

    # Bilingual release notes state the same bound twice; report it once.
    return ApplicabilityVerdict(
        status,
        list(dict.fromkeys(reasons)),
        list(dict.fromkeys(conflicts)),
        sorted(set(evidence_ids)),
    )


_SEVERITY = {
    Applicability.DIRECT_MATCH: 0,
    Applicability.COMPATIBLE_UNKNOWN: 1,
    Applicability.POSSIBLE_CONTRADICTION: 2,
    Applicability.UNRESOLVED_VERSION: 3,
    Applicability.HARD_CONTRADICTION: 4,
}


def _weaken(current: Applicability, candidate: Applicability) -> Applicability:
    return candidate if _SEVERITY[candidate] > _SEVERITY[current] else current
