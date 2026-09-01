"""Symptom → change resolution.

The first live repository has no public pull requests, so there is no
``Discussion → PR → Commit`` edge to follow. What exists is git: the commits
between two releases and the files each one touched.

An earlier version of this module scored commits by loose vocabulary overlap.
Measured against 60 real threads it linked symptoms to ``chore:`` commits,
test-only changes and README edits - confident-looking upgrade advice built on a
coincidence. That is precisely the failure this product exists to prevent, so
the rule is now much stricter and much easier to defend:

    the symptom text must name a source path, and the commit must have
    changed that path.

Anything weaker abstains. Recall drops - most threads never name a file - but a
missing answer is recoverable and a fabricated causal link is not. The result is
still labelled ``inferred``: evidence that a change touched the code the
reporter pointed at, never a maintainer's statement that it fixes the symptom.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.store.models import Release

MIN_CHANGE_SCORE = 3.0
MAX_RANGES = 6
MAX_COMMITS_PER_RANGE = 500
# A commit touching hundreds of files is a version bump or a lockfile sweep.
MAX_FILES_FOR_TARGETED_FIX = 60

# Only these commits can be *the fix*. A chore, a test-only change, a refactor
# or a release merge may sit in the same range and share vocabulary, but calling
# one of them "the change that fixes your symptom" invents causality.
DEFAULT_FIX_SUBJECT_PATTERNS = (r"^fix[:( ]", r"^perf[:( ]", r"^revert[:( ]", r"^hotfix[:( ]")

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
    ".c",
    ".cc",
    ".cpp",
    ".h",
)
# Filenames that exist in every package and therefore identify nothing.
GENERIC_BASENAMES = frozenset(
    {
        "index.ts",
        "index.tsx",
        "index.js",
        "index.mjs",
        "index.cjs",
        "types.ts",
        "utils.ts",
        "util.ts",
        "main.ts",
        "main.js",
        "mod.ts",
        "package.json",
        "tsconfig.json",
        "readme.md",
        "__init__.py",
        "setup.py",
        "constants.ts",
        "helpers.ts",
    }
)
# A path named in prose: packages/loader/src/internal.ts, ./src/foo.ts, a\b.ts
PATH_RE = re.compile(r"[\w.@-]+(?:[/\\][\w.@-]+)+\.[A-Za-z]{1,5}\b")
BARE_FILE_RE = re.compile(r"\b[\w.-]+\.(?:ts|tsx|js|jsx|mjs|cjs|py|rs|go)\b")


@dataclass
class ChangeCandidate:
    commit_sha: str
    short_sha: str
    subject: str
    files: list[str]
    score: float
    matched_paths: list[str]
    matched_tokens: list[str]
    release_tag: str
    range_base: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "commit": self.commit_sha,
            "subject": self.subject,
            "files": self.files,
            "score": round(self.score, 2),
            "matched_paths": self.matched_paths,
            "matched_tokens": self.matched_tokens,
            "first_release_in_range": self.release_tag,
            "range": f"{self.range_base}..{self.release_tag}",
            "derivation": "inferred",
        }


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip().lower().lstrip("./")


# Directories that carry no identity on their own.
GENERIC_DIRS = frozenset({"src", "lib", "dist", "build", "tests", "test", "packages", "apps"})


def _usable_tail(tail: str) -> bool:
    """`loader/src/internal.ts` identifies a place. `src/index.ts` does not."""
    parts = [p for p in tail.split("/") if p]
    if not parts:
        return False
    if parts[-1] not in GENERIC_BASENAMES:
        return True
    # A generic filename only identifies something when a real package name leads it.
    return len(parts) >= 3 and parts[0] not in GENERIC_DIRS


def symptom_paths(text: str | None) -> tuple[set[str], set[str]]:
    """Source paths the reporter actually named: (path tails, distinctive basenames)."""
    if not text:
        return set(), set()

    tails: set[str] = set()
    basenames: set[str] = set()

    for raw in PATH_RE.findall(text):
        path = _normalize_path(raw)
        if not path.endswith(SOURCE_SUFFIXES):
            continue
        parts = [p for p in path.split("/") if p]
        for width in (2, 3):
            if len(parts) >= width:
                tail = "/".join(parts[-width:])
                if _usable_tail(tail):
                    tails.add(tail)
        if parts and parts[-1] not in GENERIC_BASENAMES:
            basenames.add(parts[-1])

    for raw in BARE_FILE_RE.findall(text):
        name = _normalize_path(raw)
        if name not in GENERIC_BASENAMES:
            basenames.add(name)

    return tails, basenames


def _commit_path_forms(path: str) -> tuple[str, str | None, str | None, str]:
    normalized = _normalize_path(path)
    parts = [p for p in normalized.split("/") if p]
    tail2 = "/".join(parts[-2:]) if len(parts) >= 2 else None
    tail3 = "/".join(parts[-3:]) if len(parts) >= 3 else None
    return normalized, tail2, tail3, parts[-1] if parts else normalized


def _touches_source(files: list[str]) -> bool:
    return any(_normalize_path(f).endswith(SOURCE_SUFFIXES) for f in files)


def _is_fix_commit(subject: str, patterns: tuple[str, ...]) -> bool:
    lowered = subject.strip().lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def resolve_change(
    git: GitRepo,
    releases: list[Release],
    symptom_tokens: set[str],
    *,
    symptom_text: str | None = None,
    max_ranges: int = MAX_RANGES,
    fix_subject_patterns: tuple[str, ...] = DEFAULT_FIX_SUBJECT_PATTERNS,
) -> ChangeCandidate | None:
    """Find a fix commit that changed a source path the symptom named.

    ``releases`` must be sorted ascending by version. Every range is scanned and
    the strongest candidate wins; which release first *contains* it is settled
    afterwards by git ancestry. Returns ``None`` whenever the symptom named no
    source path, or no fix commit touched one - the caller must then abstain.
    """
    if len(releases) < 2:
        return None

    tails, basenames = symptom_paths(symptom_text)
    if not tails and not basenames:
        return None

    pairs = list(zip(releases, releases[1:], strict=False))[-max_ranges:]
    best: ChangeCandidate | None = None

    for base, head in pairs:
        commits = git.log_with_files(base.tag_name, head.tag_name, limit=MAX_COMMITS_PER_RANGE)
        for commit, files in commits:
            if not files or len(files) > MAX_FILES_FOR_TARGETED_FIX:
                continue
            if len(commit.parents) > 1:
                continue  # a merge commit is not the change
            if not _is_fix_commit(commit.subject, fix_subject_patterns):
                continue
            if not _touches_source(files):
                continue

            matched: list[str] = []
            score = 0.0
            path_anchored = False
            for path in files:
                normalized, tail2, tail3, basename = _commit_path_forms(path)
                if not normalized.endswith(SOURCE_SUFFIXES):
                    continue
                if tail3 and _usable_tail(tail3) and tail3 in tails:
                    matched.append(path)
                    score += 4.0
                    path_anchored = True
                elif tail2 and _usable_tail(tail2) and tail2 in tails:
                    matched.append(path)
                    score += 3.0
                    path_anchored = True
                elif basename in basenames:
                    # A shared filename is corroboration, never the anchor.
                    matched.append(path)
                    score += 0.5

            if not path_anchored or score < MIN_CHANGE_SCORE:
                continue

            # Subject agreement is a tie-breaker only; it can never create a link.
            subject_lower = commit.subject.lower()
            subject_hits = sorted(
                token for token in symptom_tokens if len(token) >= 5 and token in subject_lower
            )
            score += 0.5 * len(subject_hits)

            candidate = ChangeCandidate(
                commit_sha=commit.sha,
                short_sha=commit.short_sha,
                subject=commit.subject,
                files=files,
                score=score,
                matched_paths=matched,
                matched_tokens=subject_hits,
                release_tag=head.tag_name,
                range_base=base.tag_name,
                evidence={
                    "command": f"git log --name-only {base.tag_name}..{head.tag_name}",
                    "rule": "the symptom named these source paths and this fix commit changed them",
                    "derivation": "inferred",
                    "note": (
                        "path overlap between the report and the change; "
                        "not a maintainer statement that this commit fixes the symptom"
                    ),
                },
            )
            if best is None or candidate.score > best.score:
                best = candidate

    return best
