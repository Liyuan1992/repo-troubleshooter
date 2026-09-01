"""Extract explicit cross-references from body text.

Only what the text literally states. A body that links a commit produces a
``REFERENCES`` edge with derivation ``text_explicit``; nothing here ever claims
that the linked commit fixes anything. Semantic linking is a later, separately
evidenced step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SHA_RE = re.compile(r"\b(?<![\w/])([0-9a-f]{7,40})\b(?![\w/])")
NUMBER_REF_RE = re.compile(r"(?<![\w/&])#(\d{1,7})\b")
URL_RE = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)/"
    r"(discussions|issues|pull|commit|commits|releases|compare)/([^\s)\]>,\"']+)",
    re.IGNORECASE,
)
# "fixed in v0.1.2", "released in dsh-v0.1.2-alpha.3", "since 0.2.4"
VERSION_MENTION_RE = re.compile(
    r"\b(?:in|since|as of|from|until|before|after|fixed in|released in|upgrade to|downgrade to)"
    r"\s+((?:[a-z][\w.-]*[-@/])?v?\d+\.\d+(?:\.\d+)?(?:[-.][\w.]+)?)",
    re.IGNORECASE,
)

# Words that make a bare hex string not a sha (hashes in logs, hex colors, ids).
_SHA_NEGATIVE_CONTEXT = re.compile(r"(0x|#)$")


@dataclass(frozen=True)
class Reference:
    kind: str  # commit | discussion | issue | pull_request | release | compare | number | version
    value: str
    raw: str
    owner: str | None = None
    repo: str | None = None
    confidence: str = "high"


def _sha_candidates(text: str) -> list[Reference]:
    refs: list[Reference] = []
    for match in SHA_RE.finditer(text):
        sha = match.group(1)
        prefix = text[max(0, match.start() - 2) : match.start()]
        if _SHA_NEGATIVE_CONTEXT.search(prefix):
            continue
        if sha.isdigit():  # a plain number is not a sha
            continue
        # 7-char hex is common in normal text; keep it but flag lower confidence.
        confidence = "high" if len(sha) >= 12 else "medium"
        refs.append(Reference(kind="commit", value=sha, raw=match.group(0), confidence=confidence))
    return refs


def extract_references(text: str | None, *, self_repo: str | None = None) -> list[Reference]:
    """Return de-duplicated explicit references found in ``text``."""
    if not text:
        return []

    refs: list[Reference] = []

    for match in URL_RE.finditer(text):
        owner, repo, section, tail = match.groups()
        tail = tail.rstrip(".,);")
        kind_map = {
            "discussions": "discussion",
            "issues": "issue",
            "pull": "pull_request",
            "commit": "commit",
            "commits": "commit",
            "releases": "release",
            "compare": "compare",
        }
        kind = kind_map[section.lower()]
        value = tail.split("#")[0].split("?")[0]
        if kind == "release":
            value = value.removeprefix("tag/")
        refs.append(Reference(kind=kind, value=value, raw=match.group(0), owner=owner, repo=repo))

    # Strip URLs before scanning bare tokens so we do not double-count.
    stripped = URL_RE.sub(" ", text)

    for match in NUMBER_REF_RE.finditer(stripped):
        refs.append(
            Reference(
                kind="number",
                value=match.group(1),
                raw=match.group(0),
                owner=self_repo.split("/")[0] if self_repo and "/" in self_repo else None,
                repo=self_repo.split("/")[1] if self_repo and "/" in self_repo else None,
            )
        )

    refs.extend(_sha_candidates(stripped))

    for match in VERSION_MENTION_RE.finditer(stripped):
        refs.append(
            Reference(kind="version", value=match.group(1), raw=match.group(0), confidence="medium")
        )

    seen: set[tuple[str, str]] = set()
    unique: list[Reference] = []
    for ref in refs:
        key = (ref.kind, ref.value.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique
