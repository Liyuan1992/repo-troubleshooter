"""What a report is *about*, decided by how each mention is used.

Token shape cannot tell you a package's role. `@acme/thing` looks identical
whether the sentence says it crashed or says something else imports it, and
those are opposite facts:

    @acme/theme-kit crashes on startup          -> theme-kit is the subject
    @acme/theme-kit depends on @deepseek-ai/dsh -> dsh is a dependency it uses

So every package mention keeps the span that produced it and the cue that
classified it, and roles come from context:

``primary``
    The thing that failed: the subject of a failure verb (`X crashes`), the
    subject of a *named* negated action (`X did not load`), or the object of one
    (`could not resolve X`, `HTML did not preload X`).

    A bare negation proves nothing on its own - the action has to be named - and
    negating a failure verb is good news: `X does not crash` leaves X merely
    mentioned. An explicit health cue (`X is healthy`, `X is not failing`)
    outranks any failure word that happens to stand nearby.

``referenced_dependency``
    Something the report says is *used*: `depends on X`, `imports X`,
    `peer dependency X`. Scoped or not - being scoped proves nothing.

``mentioned``
    Named with no cue either way. Good for retrieval, never used to veto.

Only `primary` mentions can establish or refuse package identity. Dependencies
and bare mentions can weaken a match; they can never refuse one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

# Bumped whenever subject or behaviour extraction changes in a way that makes
# already-mined signatures wrong. The value is stored alongside the mined rows;
# a database holding an older version must be rebuilt before it may be used.
FEATURE_EXTRACTOR_VERSION = 6

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


class PackageRole(StrEnum):
    PRIMARY = "primary"
    DEPENDENCY = "referenced_dependency"
    MENTIONED = "mentioned"


# --- context cues -----------------------------------------------------------
#
# Grammar, not vocabulary. Each cue is about the relationship between a mention
# and a failure, so adding a new package name never needs a code change.

# "... depends on X", "... imports X", "peer dependency X"
DEPENDENCY_CUE_RE = re.compile(
    r"(?:depends?\s+(?:up)?on|depended\s+on|dependenc(?:y|ies)(?:\s+on)?|peer\s+dependency"
    r"|uses|using|used\s+by|imports?|importing|requires?|requiring|installs?|installing"
    r"|bundles?|bundling|pulls?\s+in|via|through|resolve[sd]?\s+to)"
    r"\s+[\"'`]?$",
    re.IGNORECASE,
)

# Verbs that *are* the failure. Negating one of these is good news, not evidence:
# "does not crash" says the thing works.
FAILURE_VERBS = (
    "crash",
    "crashes",
    "crashed",
    "crashing",
    "fail",
    "fails",
    "failed",
    "failing",
    "throw",
    "throws",
    "threw",
    "thrown",
    "throwing",
    "error",
    "errors",
    "errored",
    "break",
    "breaks",
    "broke",
    "broken",
    "breaking",
    "hang",
    "hangs",
    "hung",
    "hanging",
    "panic",
    "panics",
    "panicked",
    "die",
    "dies",
    "died",
    "abort",
    "aborts",
    "aborted",
    "regress",
    "regresses",
    "regressed",
)

# Actions a healthy system performs. Negating one of these *is* the failure:
# "did not preload" says the thing that should have happened did not.
EXPECTED_ACTIONS = (
    "load",
    "loads",
    "loaded",
    "loading",
    "preload",
    "preloads",
    "preloaded",
    "resolve",
    "resolves",
    "resolved",
    "start",
    "starts",
    "started",
    "boot",
    "boots",
    "booted",
    "mount",
    "mounts",
    "mounted",
    "render",
    "renders",
    "rendered",
    "respond",
    "responds",
    "responded",
    "return",
    "returns",
    "returned",
    "initialize",
    "initialise",
    "initializes",
    "initialised",
    "initialized",
    "connect",
    "connects",
    "connected",
    "compile",
    "compiles",
    "compiled",
    "build",
    "builds",
    "built",
    "install",
    "installs",
    "installed",
    "appear",
    "appears",
    "appeared",
    "run",
    "runs",
    "ran",
    "launch",
    "launches",
    "launched",
    "open",
    "opens",
    "opened",
    "apply",
    "applies",
    "applied",
    "register",
    "registers",
    "registered",
    "emit",
    "emits",
    "emitted",
    "find",
    "finds",
    "found",
    "work",
    "works",
    "worked",
    "come",
    "comes",
    "came",
    "show",
    "shows",
    "shown",
    "update",
    "updates",
    "updated",
)

_NEGATION = (
    r"(?:does|did|do|is|was|are|were|has|have|will|would|can|could)\s*n[o’']?t"
    r"|cannot|can\s*not|never|unable\s+to|fails?\s+to|failed\s+to"
)

# "X did not load", "X never starts" - the negated action must be named, so a
# bare "does not" carries no meaning on its own.
NEGATED_ACTION_AFTER_RE = re.compile(
    r"^[\"'`]?\s*(?:" + _NEGATION + r")\s+(?P<verb>[a-z]+)",
    re.IGNORECASE,
)

# "X crashes", "X throws"
FAILURE_VERB_AFTER_RE = re.compile(
    r"^[\"'`]?\s*(?:is\s+|was\s+|has\s+|have\s+)?"
    r"(?P<verb>" + "|".join(sorted(FAILURE_VERBS, key=len, reverse=True)) + r")\b"
    r"|^[\"'`]?\s*(?:blow(?:s)?\s+up|blew\s+up|exits?\s+with|refus(?:es|ed)\s+to"
    r"|returns?\s+(?:an?\s+)?(?:error|nothing|undefined|null)"
    r"|report(?:s|ed)?\s+(?:an?\s+)?(?:error|failure))",
    re.IGNORECASE,
)

# "could not resolve X", "HTML did not preload X" - again, the action is named.
NEGATED_ACTION_BEFORE_RE = re.compile(
    r"(?:" + _NEGATION + r")\s+(?P<verb>[a-z]+)(?:\s+\w+){0,2}\s+[\"'`]?$",
    re.IGNORECASE,
)

# "error in X", "raised from X", "crashes in X" - a failure located at X.
FAILURE_LOCATION_BEFORE_RE = re.compile(
    r"(?:error\s+(?:in|loading|from)|problem\s+(?:in|with)|crash(?:es|ed)?\s+in"
    r"|thrown\s+by|raised\s+(?:from|by)|coming\s+from|broken\s+in|missing)"
    r"\s+[\"'`]?$",
    re.IGNORECASE,
)

# Explicit counter-evidence: the report says this one is fine. It outranks any
# failure word that merely happens to be nearby.
HEALTH_CUE_RE = re.compile(
    r"\b(?:is\s+)?(?:healthy|fine|ok|okay|working|works\s+(?:fine|well)|unaffected"
    r"|resolved|fixed|passing|passes|green|stable|up\s+to\s+date|not\s+(?:the\s+)?"
    r"(?:cause|culprit|problem|issue))\b"
    r"|\b(?:does|did|is|was|are|were)\s*n[o\u2019']?t\s+(?:"
    + "|".join(sorted(FAILURE_VERBS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

SCOPED_PACKAGE_RE = re.compile(r"@[\w.-]+/[\w.-]+(?:/[\w.-]+)*")
BARE_PACKAGE_RE = re.compile(r"\b([a-z][\w.-]{2,})@[\^~>=<]*\d[\w.-]*")
BUILTIN_RE = re.compile(r"\bnode:[\w/]+")
HYPHEN_TOKEN_RE = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+){1,4})\b")

_QUOTED = "[`\"']"
_SYNTAX_PROOF_TEMPLATES = (
    _QUOTED + "{token}" + _QUOTED,
    r"\b{token}[/\\.][\w./\\-]+",
    r"\b{token}@[\w.-]+",
    r"(?:^|\n)\s*{token}\s*:",
    r"\b(?:package|module|plugin|vendor|import|require|from|dependency)\s+" + _QUOTED + r"?{token}",
)

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

# How much text around a mention counts as its context.
LOOKBEHIND = 48
LOOKAHEAD = 40


@dataclass(frozen=True)
class PackageMention:
    """One package name, the span that produced it, and why it got its role."""

    name: str
    role: PackageRole
    start: int
    end: int
    cue: str
    context: str

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "role": str(self.role),
            "span": [self.start, self.end],
            "cue": self.cue,
            "context": self.context,
        }


@dataclass
class Subjects:
    """The named things a report is about, by role."""

    package_mentions: list[PackageMention] = field(default_factory=list)
    paths: set[str] = field(default_factory=set)
    builtins: set[str] = field(default_factory=set)
    modules: set[str] = field(default_factory=set)

    def _names(self, role: PackageRole) -> set[str]:
        return {m.name for m in self.package_mentions if m.role is role}

    @property
    def primary_packages(self) -> set[str]:
        return self._names(PackageRole.PRIMARY)

    @property
    def dependencies(self) -> set[str]:
        return self._names(PackageRole.DEPENDENCY)

    @property
    def mentioned_packages(self) -> set[str]:
        return self._names(PackageRole.MENTIONED)

    @property
    def all_packages(self) -> set[str]:
        return {m.name for m in self.package_mentions}

    @property
    def all(self) -> set[str]:
        return self.all_packages | self.paths | self.builtins | self.modules

    @property
    def identifying(self) -> set[str]:
        """Roles that may establish identity. Builtins are excluded on purpose."""
        return self.primary_packages | self.paths | self.modules

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


def _negated_action_is_failure(verb: str) -> bool:
    """Is "did not <verb>" evidence of a failure?

    Negating a failure verb is good news - "does not crash" says it works - so
    only the negation of an expected action counts.
    """
    lowered = verb.lower()
    if lowered in FAILURE_VERBS:
        return False
    return lowered in EXPECTED_ACTIONS


def classify_mention(text: str, start: int, end: int, name: str) -> PackageMention:
    """Decide a mention's role from the words immediately around it."""
    before = text[max(0, start - LOOKBEHIND) : start]
    after = text[end : end + LOOKAHEAD]

    def build(role: PackageRole, cue: str) -> PackageMention:
        return PackageMention(
            name=name,
            role=role,
            start=start,
            end=end,
            cue=cue,
            context=(before[-32:] + text[start:end] + after[:32]).strip(),
        )

    # Explicit counter-evidence wins over any failure word merely standing near
    # this mention: "@scope/lib is healthy" is not a report that it failed.
    health = HEALTH_CUE_RE.search(before[-24:]) or HEALTH_CUE_RE.search(after)
    healthy = health.group(0).strip() if health else ""

    dependency_cue = DEPENDENCY_CUE_RE.search(before)
    if dependency_cue:
        cue = dependency_cue.group(0).strip()
        return build(PackageRole.DEPENDENCY, f"{cue} (+healthy: {healthy})" if healthy else cue)

    if healthy:
        return build(PackageRole.MENTIONED, f"healthy: {healthy}")

    # "X did not preload" - the negated action has to be named and has to be an
    # action the system was expected to perform.
    negated_after = NEGATED_ACTION_AFTER_RE.match(after)
    if negated_after:
        verb = negated_after.group("verb")
        if _negated_action_is_failure(verb):
            return build(PackageRole.PRIMARY, negated_after.group(0).strip())
        # "X does not crash" - explicitly not a failure.
        return build(PackageRole.MENTIONED, f"negated failure: {negated_after.group(0).strip()}")

    failure_after = FAILURE_VERB_AFTER_RE.match(after)
    if failure_after:
        return build(PackageRole.PRIMARY, failure_after.group(0).strip())

    negated_before = NEGATED_ACTION_BEFORE_RE.search(before)
    if negated_before:
        verb = negated_before.group("verb")
        if _negated_action_is_failure(verb):
            return build(PackageRole.PRIMARY, negated_before.group(0).strip())
        return build(PackageRole.MENTIONED, f"negated failure: {negated_before.group(0).strip()}")

    location_before = FAILURE_LOCATION_BEFORE_RE.search(before)
    if location_before:
        return build(PackageRole.PRIMARY, location_before.group(0).strip())

    return build(PackageRole.MENTIONED, "")


def _package_aliases(name: str) -> list[str]:
    """`@scope/pkg/file.js` also stands for `@scope/pkg`."""
    aliases = [name]
    if name.startswith("@") and name.count("/") >= 2:
        scope, _, rest = name.partition("/")
        aliases.append(f"{scope}/{rest.split('/')[0]}")
    return aliases


def classify(text: str, known_modules: frozenset[str] = frozenset()) -> Subjects:
    """Split every named subject in ``text`` into its role."""
    subjects = Subjects()
    lowered = text.lower()

    for match in BUILTIN_RE.finditer(lowered):
        subjects.builtins.add(match.group(0))

    seen: set[tuple[str, int]] = set()
    for match in SCOPED_PACKAGE_RE.finditer(lowered):
        mention = classify_mention(lowered, match.start(), match.end(), match.group(0))
        for alias in _package_aliases(mention.name):
            if (alias, match.start()) in seen:
                continue
            seen.add((alias, match.start()))
            subjects.package_mentions.append(
                PackageMention(
                    name=alias,
                    role=mention.role,
                    start=mention.start,
                    end=mention.end,
                    cue=mention.cue,
                    context=mention.context,
                )
            )

    for match in BARE_PACKAGE_RE.finditer(lowered):
        name = match.group(1)
        if name.startswith("node:") or any(name == m.name for m in subjects.package_mentions):
            continue
        mention = classify_mention(lowered, match.start(1), match.end(1), name)
        # A bare `name@version` with no failure cue reads as something installed.
        if mention.role is PackageRole.MENTIONED:
            mention = PackageMention(
                name=name,
                role=PackageRole.DEPENDENCY,
                start=mention.start,
                end=mention.end,
                cue="name@version",
                context=mention.context,
            )
        subjects.package_mentions.append(mention)

    for path in iter_source_paths(lowered):
        tail = identifying_path_tail(path)
        if tail:
            subjects.paths.add(tail)

    named_elsewhere = subjects.all_packages | subjects.paths | subjects.builtins
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
    subjects.modules -= subjects.all_packages

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
