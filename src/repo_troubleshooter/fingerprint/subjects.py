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

    A bare negation proves nothing on its own - the negated thing has to be
    named - and what it is decides the meaning: negating a failure verb is good
    news (`X does not crash`), while negating an expected action (`X did not
    load`) or a positive state (`X is not working`) is the failure itself.

    Every cue is bound to the mention's own clause and anchored to it, so a
    health cue attached to one package cannot describe the next one.

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
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

# Bumped whenever subject or behaviour extraction changes in a way that makes
# already-mined signatures wrong. The value is stored alongside the mined rows;
# a database holding an older version must be rebuilt before it may be used.
FEATURE_EXTRACTOR_VERSION = 7

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

# Positive states. Saying one holds is health; saying one does *not* hold is a
# failure report, which is why `not working` must never read as "working".
POSITIVE_STATES = (
    "working",
    "works",
    "healthy",
    "ok",
    "okay",
    "fine",
    "stable",
    "passing",
    "passes",
    "green",
    "fixed",
    "resolved",
    "unaffected",
    "correct",
    "present",
    "available",
    "enabled",
    "up to date",
    "up-to-date",
)

_NEGATION = (
    r"(?:does|did|do|is|was|are|were|has|have|will|would|can|could)\s*n[o\u2019']?t"
    r"|cannot|can\s*not|never|unable\s+to|fails?\s+to|failed\s+to"
)

# The negated thing, captured so its meaning can be judged. `up to date` is
# multi-word, so it is matched explicitly rather than as a bare verb.
_NEGATED_TARGET = r"(?P<verb>up[\s-]to[\s-]date|[a-z]+)"

# "X did not load", "X is not working" - anchored at the start of X's own clause.
NEGATED_AFTER_RE = re.compile(
    r"^[\"'`]?\s*(?:" + _NEGATION + r")\s+" + _NEGATED_TARGET,
    re.IGNORECASE,
)

# "could not resolve X" - anchored at the end of the clause leading into X.
NEGATED_BEFORE_RE = re.compile(
    r"(?:" + _NEGATION + r")\s+" + _NEGATED_TARGET + r"(?:\s+\w+){0,2}\s+[\"'`]?$",
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

# "error in X", "raised from X" - a failure located at X.
FAILURE_LOCATION_BEFORE_RE = re.compile(
    r"(?:error\s+(?:in|loading|from)|problem\s+(?:in|with)|crash(?:es|ed)?\s+in"
    r"|thrown\s+by|raised\s+(?:from|by)|coming\s+from|broken\s+in|missing)"
    r"\s+[\"'`]?$",
    re.IGNORECASE,
)

# A plain assertion of health, with no negation in front of it. Negations are
# handled first and separately, so this only ever sees the affirmative case.
_POSITIVE_ALTERNATIVES = "|".join(sorted(POSITIVE_STATES, key=len, reverse=True)).replace(
    " ", r"\s+"
)
HEALTH_AFTER_RE = re.compile(
    r"^[\"'`]?\s*(?:is|was|are|were|looks?|seems?|remains?)?\s*(?:"
    + _POSITIVE_ALTERNATIVES
    + r")\b",
    re.IGNORECASE,
)
HEALTH_BEFORE_RE = re.compile(
    r"\b(?:" + _POSITIVE_ALTERNATIVES + r")\s+[\"'`]?$",
    re.IGNORECASE,
)

# Where one mention's context ends and the next begins.
CLAUSE_BREAK_RE = re.compile(r"[.;:!?\n]|\band\b|\bbut\b|\bwhile\b|\bhowever\b|,")

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


def _negated_meaning(target: str) -> PackageRole:
    """What does "not <target>" say about the thing it is attached to?

    Three different sentences, three different answers:

        does not crash    negating a failure  -> good news, not the subject
        did not load      negating an action  -> the failure itself
        is not working    negating a state    -> the failure itself
    """
    lowered = re.sub(r"[\s-]+", " ", target.lower().strip())
    if lowered in FAILURE_VERBS:
        return PackageRole.MENTIONED
    if lowered in POSITIVE_STATES or lowered in EXPECTED_ACTIONS:
        return PackageRole.PRIMARY
    # An unrecognised word proves nothing either way.
    return PackageRole.MENTIONED


def clause_window(
    text: str, start: int, end: int, other_spans: Sequence[tuple[int, int]]
) -> tuple[str, str]:
    """The text that belongs to *this* mention, before and after it.

    A cue may only speak for the mention it is attached to, so the window stops
    at the first clause break and never crosses into another package's mention.
    Without this, `@a/x is healthy, @b/y crashes` lets `@a/x`'s health cue
    describe `@b/y`.
    """
    left_limit = max(0, start - LOOKBEHIND)
    right_limit = min(len(text), end + LOOKAHEAD)

    for other_start, other_end in other_spans:
        if other_end <= start:
            left_limit = max(left_limit, other_end)
        elif other_start >= end:
            right_limit = min(right_limit, other_start)

    before = text[left_limit:start]
    after = text[end:right_limit]

    breaks = [m.end() for m in CLAUSE_BREAK_RE.finditer(before)]
    if breaks:
        before = before[breaks[-1] :]

    first_break = CLAUSE_BREAK_RE.search(after)
    if first_break:
        after = after[: first_break.start()]

    return before, after


def classify_mention(
    text: str,
    start: int,
    end: int,
    name: str,
    other_spans: Sequence[tuple[int, int]] = (),
) -> PackageMention:
    """Decide a mention's role from the clause it appears in.

    Every cue below is anchored: an "after" cue must start the mention's own
    clause, a "before" cue must end it. No cue is looked for by scanning a
    character window, because that is how one package's health cue ended up
    describing the next one.
    """
    before, after = clause_window(text, start, end, other_spans)

    def build(role: PackageRole, cue: str) -> PackageMention:
        return PackageMention(
            name=name,
            role=role,
            start=start,
            end=end,
            cue=cue,
            context=(before[-32:] + text[start:end] + after[:32]).strip(),
        )

    dependency_cue = DEPENDENCY_CUE_RE.search(before)
    if dependency_cue:
        return build(PackageRole.DEPENDENCY, dependency_cue.group(0).strip())

    # Negation is resolved before any bare health cue, so `is not working` can
    # never be read as `working`.
    negated_after = NEGATED_AFTER_RE.match(after)
    if negated_after:
        role = _negated_meaning(negated_after.group("verb"))
        cue = negated_after.group(0).strip()
        return build(role, cue if role is PackageRole.PRIMARY else f"healthy: {cue}")

    health_after = HEALTH_AFTER_RE.match(after)
    if health_after:
        return build(PackageRole.MENTIONED, f"healthy: {health_after.group(0).strip()}")

    failure_after = FAILURE_VERB_AFTER_RE.match(after)
    if failure_after:
        return build(PackageRole.PRIMARY, failure_after.group(0).strip())

    negated_before = NEGATED_BEFORE_RE.search(before)
    if negated_before:
        role = _negated_meaning(negated_before.group("verb"))
        cue = negated_before.group(0).strip()
        return build(role, cue if role is PackageRole.PRIMARY else f"healthy: {cue}")

    health_before = HEALTH_BEFORE_RE.search(before)
    if health_before:
        return build(PackageRole.MENTIONED, f"healthy: {health_before.group(0).strip()}")

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

    # Every package span, so one mention's cue cannot reach across another.
    spans = [(m.start(), m.end()) for m in SCOPED_PACKAGE_RE.finditer(lowered)]
    spans += [(m.start(1), m.end(1)) for m in BARE_PACKAGE_RE.finditer(lowered)]

    seen: set[tuple[str, int]] = set()
    for match in SCOPED_PACKAGE_RE.finditer(lowered):
        others = [s for s in spans if s != (match.start(), match.end())]
        mention = classify_mention(lowered, match.start(), match.end(), match.group(0), others)
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
        others = [s for s in spans if s != (match.start(1), match.end(1))]
        mention = classify_mention(lowered, match.start(1), match.end(1), name, others)
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
