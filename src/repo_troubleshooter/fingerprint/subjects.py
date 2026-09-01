"""What a report is *about*, with a type and a strength.

`customer-facing` and `time-sensitive` are adjectives. `theme-parser` is a
module. A regex over hyphens cannot tell them apart, so this module asks for
evidence instead:

**strong** - a scoped package (`@deepseek-ai/dsh-client-modules`, `node:path`) or
a source path carrying an identifying directory (`loader/src/internal.ts`).
These name a place in the tree, and a conflict between two of them is decisive.

**weak** - a module name, admitted only when something proves it is one:

* *syntax* - the text uses it as code does: in backticks or quotes, with a path
  or extension, with an `@version`, as a log prefix (`client-modules: ...`), or
  after `package`/`module`/`plugin`/`import`/`from`;
* *morphology* - the last segment names a thing rather than describing one: an
  agent noun (`-parser`, `-loader`, `-renderer`) or a known component noun
  (`-modules`, `-shim`, `-plugin`). Adjectival endings (`-facing`, `-sensitive`,
  `-widening`) are rejected;
* *corpus* - the repository's own mined subjects already contain the name.

Anything else is left to the plain text and structural features, where it can
contribute to retrieval but can never veto an identity decision.
"""

from __future__ import annotations

import re

# Bumped whenever subject or behaviour extraction changes in a way that makes
# already-mined signatures wrong. The value is stored alongside the mined rows;
# a database holding an older version must be rebuilt before it may be used.
FEATURE_EXTRACTOR_VERSION = 3

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


def module_names_from_subjects(subjects: set[str]) -> set[str]:
    """Corpus evidence: module names implied by strong subjects already mined."""
    names: set[str] = set()
    for subject in subjects:
        if subject.startswith("@") and "/" in subject:
            names.add(subject.split("/", 1)[1].split("/")[0])
        elif "/" in subject:
            names.add(subject.split("/")[0])
    return {name for name in names if "-" in name and len(name) >= 6}
