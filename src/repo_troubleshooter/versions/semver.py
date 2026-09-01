"""Version normalisation and comparison.

Real repositories do not tag versions the way a spec diagram does:
``dsh-v0.1.2-alpha.3``, ``v0.2.3``, ``release-1.4``, ``0.5.0.post1``. We
normalise to a comparable form and keep the original string, and we never
guess: an unparseable version returns ``None`` so the applicability layer can
report ``unresolved_version`` instead of inventing an ordering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

# Strip common tag decoration: dsh-v1.2.3, v1.2.3, release-1.2.3, pkg@1.2.3
_PREFIX_RE = re.compile(
    r"^\s*(?:[A-Za-z][A-Za-z0-9._-]*?[-/@])?(?:v|version[-_]?|release[-_]?)?(?=\d)",
    re.IGNORECASE,
)
_TRAILING_JUNK_RE = re.compile(r"[\s)\],;]+$")


def normalize_version(raw: str | None) -> str | None:
    """Return a comparable version string, or None when we cannot be sure."""
    if not raw:
        return None
    candidate = _TRAILING_JUNK_RE.sub("", raw.strip())
    candidate = _PREFIX_RE.sub("", candidate, count=1)
    if not candidate or not candidate[0].isdigit():
        return None
    try:
        return str(Version(candidate))
    except InvalidVersion:
        return None


def parse(raw: str | None) -> Version | None:
    normalized = normalize_version(raw)
    if normalized is None:
        return None
    try:
        return Version(normalized)
    except InvalidVersion:  # pragma: no cover - normalize already validated
        return None


def compare(left: str | None, right: str | None) -> int | None:
    """-1 / 0 / 1, or None when either side is unparseable."""
    a, b = parse(left), parse(right)
    if a is None or b is None:
        return None
    return (a > b) - (a < b)


def is_prerelease(raw: str | None) -> bool | None:
    v = parse(raw)
    return None if v is None else v.is_prerelease


def sort_key(raw: str | None) -> tuple[int, Version | None, str]:
    """Sortable key that pushes unparseable versions to the end deterministically."""
    v = parse(raw)
    return (1, None, raw or "") if v is None else (0, v, raw or "")


@dataclass(frozen=True)
class VersionRange:
    """Inclusive-exclusive range used by affected/fixed constraints.

    ``None`` on either bound means unbounded. ``contains`` returns ``None``
    when the candidate cannot be parsed - unknown is not the same as false.
    """

    min_inclusive: str | None = None
    max_exclusive: str | None = None

    def contains(self, raw: str | None) -> bool | None:
        v = parse(raw)
        if v is None:
            return None
        if self.min_inclusive is not None:
            lo = parse(self.min_inclusive)
            if lo is None:
                return None
            if v < lo:
                return False
        if self.max_exclusive is not None:
            hi = parse(self.max_exclusive)
            if hi is None:
                return None
            if v >= hi:
                return False
        return True

    def __str__(self) -> str:
        lo = self.min_inclusive or "*"
        hi = self.max_exclusive or "*"
        return f">={lo},<{hi}" if self.max_exclusive else f">={lo}"
