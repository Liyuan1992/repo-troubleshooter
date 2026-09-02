"""What a report is *about*, classified by role.

A flat "strong subject" set was wrong: it let a shared `node:path` or a shared
source path cancel a conflict between two different scoped packages. Subjects
therefore carry a role, and each role has its own authority:

``package`` - a scoped package (`@deepseek-ai/dsh-client-modules`). The primary
    subject. Two reports about different packages are about different things,
    and nothing else may override that.

``path`` - a source path carrying an identifying directory
    (`loader/src/internal.ts`). Names a place in the tree; can veto, but only
    when the packages do not already agree.

``dependency`` - a package named as something the report *uses* (`react@^19`,
    "peer dependency x"). Referenced, not the subject: a mismatch weakens a
    match, it does not refuse one.

``builtin`` - `node:path`, `node:fs`. Every Node program touches these, so they
    can never establish identity and never veto it.

``module`` - a bare module name (`theme-parser`), admitted only with corpus,
    syntax or morphology evidence. Helps retrieval and scoring; a mismatch
    raises the bar for acceptance but never refuses on its own.

`customer-facing` and `time-sensitive` are none of these. A hyphen is not
evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Bumped whenever subject or behaviour extraction changes in a way that makes
# already-mined signatures wrong. The value is stored alongside the mined rows;
# a database holding an older version must be rebuilt before it may be used.
FEATURE_EXTRACTOR_VERSION = 4

# Directories every package has; a path tail led by one of these names nothing.
GENERIC_PATH_DIRS = frozenset(
    {
        "src",
        "lib",
        "dist",
        "build",
        "out",
        "bin",
        "tests",
        "test",
        "spec",
        "packages",
        "apps",
        "vendor",
        "node_modules",
        "internal",
    }
)

# Nouns that name a piece of software rather than describe one.
MODULE_NOUNS = frozenset(
    {
        "adapter",
        "agent",
        "api",
        "bridge",
        "cache",
        "cli",
        "client",
        "config",
        "core",
        "daemon",
        "gateway",
        "harness",
        "index",
        "kit",
        "layer",
        "lib",
        "module",
        "package",
        "parser",
        "plugin",
        "preset",
        "provider",
        "proxy",
        "queue",
        "registry",
        "runtime",
        "sdk",
        "server",
        "service",
        "shim",
        "store",
        "tool",
        "ui",
        "util",
        "worker",
    }
)

# Participles and adjectives: customer-facing, time-sensitive, non-widening.
ADJECTIVAL_SUFFIXES = (
    "ing",
    "ed",
    "ive",
    "able",
    "ible",
    "ful",
    "less",
    "ous",
    "ary",
    "ent",
    "ant",
    "istic",
    "wide",
)

SCOPED_PACKAGE_RE = re.compile(r"@[\w.-]+/[\w.-]+(?:/[\w.-]+)*")
BUILTIN_RE = re.compile(r"\bnode:[\w/]+")
# `react@^19`, `left-pad@1.2.3`, and names introduced as dependencies.
DEPENDENCY_VERSIONED_RE = re.compile(r"\b([a-z][\w.-]{2,})@[\^~>=<]*\d[\w.-]*")
DEPENDENCY_CONTEXT_RE = re.compile(
    r"\b(?:peer\s+)?dependenc(?:y|ies)\s+(?:on\s+)?[\"'`]?([@\w./-]{3,})",
    re.IGNORECASE,
)

_QUOTED = "[`\"']"
_SYNTAX_PROOF_TEMPLATES = (
    # `theme-parser`, "theme-parser", 'theme-parser'
    _QUOTED + "{token}" + _QUOTED,
    # theme-parser/src/index.ts, theme-parser.ts
    r"\b{token}[/\\.][\w./\\-]+",
    # theme-parser@1.4.0
    r"\b{token}@[\w.-]+",
    # a log prefix at the start of a line: "client-modules: HTML did not preload"
    r"(?:^|\n)\s*{token}\s*:",
    # the package theme-parser / import x from theme-parser
    r"\b(?:package|module|plugin|vendor|import|require|from|dependency)\s+" + _QUOTED + r"?{token}",
)

HYPHEN_TOKEN_RE = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+){1,4})\b")

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


@dataclass
class Subjects:
    """The named things a report is about, by role."""

    packages: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    dependencies: set[str] = field(default_factory=set)
    builtins: set[str] = field(default_factory=set)
    modules: set[str] = field(default_factory=set)

    @property
    def all(self) -> set[str]:
        return self.packages | self.paths | self.dependencies | self.builtins | self.modules

    @property
    def identifying(self) -> set[str]:
        """Roles that may establish identity. Builtins are excluded on purpose."""
        return self.packages | self.paths | self.modules

    def is_empty(self) -> bool:
        return not self.all


def iter_source_paths(text: str) -> list[str]:
    """Tokens that look like a source path with at least one directory.

    Split rather than matched with nested quantifiers: a regex of the shape
    `[\\w.@-]+(?:[/\\\\][\\w.@-]+)+\\.ext` backtracks catastrophically on logs.
    """
    found: list[str] = []
    for token in _TOKEN_SPLIT_RE.split(text):
        cleaned = token.strip("'\"`.,;")
        if ("/" in cleaned or chr(92) in cleaned) and cleaned.lower().endswith(SOURCE_SUFFIXES):
            found.append(cleaned)
    return found


def _is_adjectival(segment: str) -> bool:
    return any(segment.endswith(suffix) for suffix in ADJECTIVAL_SUFFIXES)


def _is_agent_noun(segment: str) -> bool:
    """parser, loader, renderer, exporter - and their plurals."""
    return len(segment) >= 5 and segment.endswith(("er", "or", "ers", "ors"))


def _is_module_noun(segment: str) -> bool:
    singular = segment[:-1] if segment.endswith("s") and len(segment) > 3 else segment
    return segment in MODULE_NOUNS or singular in MODULE_NOUNS


def morphology_proves_module(token: str) -> bool:
    """Does the final segment name a thing rather than describe one?"""
    segments = token.split("-")
    if len(segments) < 2 or any(len(segment) < 2 for segment in segments):
        return False
    last = segments[-1]
    if _is_adjectival(last):
        return False
    return _is_agent_noun(last) or _is_module_noun(last)


def syntax_proves_module(token: str, text: str) -> bool:
    """Does the surrounding text use the token the way code uses a module name?"""
    escaped = re.escape(token)
    for template in _SYNTAX_PROOF_TEMPLATES:
        if re.search(template.format(token=escaped), text, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def identifying_path_tail(path: str) -> str | None:
    """`vendor/esm-shim/src/resolve.ts` -> `esm-shim/src/resolve.ts`; `src/x.ts` -> None."""
    parts = [part for part in path.replace(chr(92), "/").split("/") if part]
    if len(parts) < 2:
        return None
    if parts[-2] not in GENERIC_PATH_DIRS:
        return "/".join(parts[-2:])
    if len(parts) >= 3 and parts[-3] not in GENERIC_PATH_DIRS:
        return "/".join(parts[-3:])
    return None


def classify(text: str, known_modules: frozenset[str] = frozenset()) -> Subjects:
    """Split every named subject in ``text`` into its role."""
    subjects = Subjects()
    lowered = text.lower()

    # --- runtime builtins first, so they never masquerade as packages -------
    for match in BUILTIN_RE.finditer(lowered):
        subjects.builtins.add(match.group(0))

    # --- primary scoped packages -------------------------------------------
    for match in SCOPED_PACKAGE_RE.finditer(lowered):
        value = match.group(0)
        subjects.packages.add(value)
        if value.count("/") >= 1:
            scope, _, rest = value.partition("/")
            subjects.packages.add(f"{scope}/{rest.split('/')[0]}")

    # --- referenced dependencies -------------------------------------------
    for match in DEPENDENCY_VERSIONED_RE.finditer(lowered):
        name = match.group(1)
        if name not in subjects.packages and not name.startswith("node:"):
            subjects.dependencies.add(name)
    for match in DEPENDENCY_CONTEXT_RE.finditer(text):
        name = match.group(1).lower().strip("\"'`")
        if name.startswith("@"):
            continue  # a scoped dependency is still a package; keep it primary
        if len(name) >= 3:
            subjects.dependencies.add(name)

    # --- source paths -------------------------------------------------------
    for path in iter_source_paths(lowered):
        tail = identifying_path_tail(path)
        if tail:
            subjects.paths.add(tail)

    # --- weak module names --------------------------------------------------
    named_elsewhere = subjects.packages | subjects.paths | subjects.builtins
    for match in HYPHEN_TOKEN_RE.finditer(lowered):
        token = match.group(1)
        if len(token) < 6 or any(token in value for value in named_elsewhere):
            continue
        if (
            token in known_modules
            or syntax_proves_module(token, text)
            or morphology_proves_module(token)
        ):
            subjects.modules.add(token)
    subjects.modules -= subjects.dependencies

    return subjects


def module_names_from_subjects(values: set[str]) -> set[str]:
    """Corpus evidence: module names implied by package and path subjects."""
    names: set[str] = set()
    for value in values:
        if value.startswith("@") and "/" in value:
            names.add(value.split("/", 1)[1].split("/")[0])
        elif "/" in value:
            names.add(value.split("/")[0])
    return {name for name in names if "-" in name and len(name) >= 6}
