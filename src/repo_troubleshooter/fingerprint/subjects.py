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
FEATURE_EXTRACTOR_VERSION = 14

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


class PackageRelation(StrEnum):
    """How the report says the package is connected to the system."""

    #: The report says it is used: `depends on X`, `imports X`, `installed X`.
    DEPENDENCY = "dependency"
    #: The report talks about it as the thing itself, not as something used.
    DIRECT = "direct"
    UNKNOWN = "unknown"


class PackageState(StrEnum):
    """What the report says about the package's condition.

    Independent of the relation: `dependency + healthy` and
    `dependency + failing` are both sayable, and one must never overwrite the
    other. Collapsing them into a single role is what let a report that
    explicitly cleared a package still authorise an upgrade of it.
    """

    #: The report says it failed.
    FAILING = "failing"
    #: The report says it is fine.
    HEALTHY = "healthy"
    #: The report says both.
    CONFLICTED = "conflicted"
    UNKNOWN = "unknown"


class PackageRole(StrEnum):
    """The single label derived from (relation, state), kept for reporting.

    Derived, never stored as the source of truth: the two axes above are.
    """

    PRIMARY = "primary"
    DEPENDENCY = "referenced_dependency"
    CONFIRMED_NON_PRIMARY = "confirmed_non_primary"
    CONFLICTED = "conflicted_subject"
    UNRESOLVED = "unresolved_subject"


def derive_role(relation: PackageRelation, state: PackageState, ambiguous: bool) -> PackageRole:
    """Collapse the two axes for display. The gate reads the axes, not this."""
    if state is PackageState.CONFLICTED:
        return PackageRole.CONFLICTED
    if state is PackageState.FAILING:
        return PackageRole.PRIMARY
    if ambiguous:
        # A failure was stated nearby and we could not attribute it. Refusing to
        # call this a plain dependency is the whole point.
        return PackageRole.UNRESOLVED
    if state is PackageState.HEALTHY:
        return PackageRole.CONFIRMED_NON_PRIMARY
    if relation is PackageRelation.DEPENDENCY:
        return PackageRole.DEPENDENCY
    return PackageRole.UNRESOLVED


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
    r"|wo\s*n[o\u2019']?t|sha\s*n[o\u2019']?t|ai\s*n[o\u2019']?t"
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

# A trailing sentence period is not part of the name: `@scope/pkg.` is `@scope/pkg`.
SCOPED_PACKAGE_RE = re.compile(r"@[\w.-]*[\w-]/[\w.-]*[\w-](?:/[\w.-]*[\w-])*")
BARE_PACKAGE_RE = re.compile(r"\b([a-z][\w.-]{2,})@[\^~>=<]*\d[\w.-]*")
BUILTIN_RE = re.compile(r"\bnode:[\w/]+")
# `name@version` only becomes a dependency when the sentence says it is one.
INSTALL_CONTEXT_RE = re.compile(
    r"\b(?:install(?:ed|ing|s)?|depend(?:s|ed|ency|encies)?|uses?|using|require[sd]?"
    r"|bundled?|pinned?|upgrade[sd]?|resolved\s+to|version)\b[^.\n]{0,40}$",
    re.IGNORECASE,
)

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
    """One package name, the span that produced it, and why it got its facts."""

    name: str
    #: The package this mention is about, with any sub-path stripped, so
    #: `@scope/pkg/file.js` and `@scope/pkg` aggregate together.
    canonical: str
    relation: PackageRelation
    state: PackageState
    start: int
    end: int
    cue: str
    context: str
    #: A failure was stated nearby that could not be attributed to a subject.
    ambiguous: bool = False

    @property
    def role(self) -> PackageRole:
        return derive_role(self.relation, self.state, self.ambiguous)

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "canonical": self.canonical,
            "relation": str(self.relation),
            "state": str(self.state),
            "role": str(self.role),
            "span": [self.start, self.end],
            "cue": self.cue,
            "context": self.context,
            "ambiguous": self.ambiguous,
        }


@dataclass
class Subjects:
    """The named things a report is about, by role."""

    package_mentions: list[PackageMention] = field(default_factory=list)
    state_assertions: list[StateAssertion] = field(default_factory=list)
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
        """Every package the report says is used - whatever its state."""
        return {
            m.name
            for m in self.package_mentions
            if m.relation is PackageRelation.DEPENDENCY and not m.ambiguous
        }

    @property
    def healthy_packages(self) -> set[str]:
        """Every package the report says is fine - including dependencies.

        This is the fact the old single-role model destroyed.
        """
        return {m.name for m in self.package_mentions if m.state is PackageState.HEALTHY}

    @property
    def conflicted_packages(self) -> set[str]:
        """The report contradicts itself about these. Never actionable."""
        return self._names(PackageRole.CONFLICTED)

    @property
    def unresolved_packages(self) -> set[str]:
        """Named, role undetermined. The identity gate must treat these as risk."""
        return self._names(PackageRole.UNRESOLVED)

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

    @property
    def uninterpreted_assertions(self) -> list[StateAssertion]:
        """Claims we could see but not read.

        Something was asserted about a package and we do not know what. That is
        risk, not silence: it is exactly how an unfamiliar verb used to slip
        through as a harmless dependency.
        """
        return [
            a
            for a in self.state_assertions
            if a.binding in (BINDING_UNINTERPRETED, BINDING_UNINTERPRETED_WEAK)
        ]

    @property
    def pointed_unread_assertions(self) -> list[StateAssertion]:
        """Unread claims whose subject points at a package: `It is operational`.

        These block that package unconditionally. A shared primary earlier in the
        report does not settle what a later sentence was trying to say about it.
        """
        return [a for a in self.state_assertions if a.binding == BINDING_UNINTERPRETED]

    @property
    def unresolved_assertions(self) -> list[StateAssertion]:
        """Condition claims we could not attach to anything.

        While one of these exists, a dependency plus a shared path, module or
        symbol must not authorise an action: something in this report is broken
        and we do not know what.
        """
        return [a for a in self.state_assertions if a.binding == BINDING_UNRESOLVED]

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


# Coordinators that continue a predicate about the *same* subject:
# "X starts but crashes", "X loads, then crashes", "X: crashes", "X, which crashes".
PREDICATE_LINK_RE = re.compile(
    r"(?:[,;:]|\band\b|\bbut\b|\bthen\b|\byet\b|\bwhich\b|\bthat\b|\bwhen\b|-{1,2})\s*",
    re.IGNORECASE,
)

# Only a sentence ends a mention's predicate chain. Commas and conjunctions do
# not: they usually continue it.
# A sentence break, not a decimal point: `nebula-theme@1.2.3` must not read as
# four sentences, which truncated the predicate window right after the version.
SENTENCE_BREAK_RE = re.compile(r"[.!?](?=\s|$)|[\n]")

# "stopped working", "ceased responding": cessation of a normal activity.
CESSATION_RE = re.compile(
    r"^[\"'`]?\s*(?:stopped|ceased|quit|gave\s+up)\s+(?P<verb>[a-z]+)",
    re.IGNORECASE,
)


# Subjects that refer back to the package just named: "import X, but it crashes",
# "use X; the package fails". Anything else - "the server crashes" - is a
# different subject and must not be attributed to X.
COREFERENCE_RE = re.compile(
    r"^(?:it|they|which|"
    r"(?:this|that|the)\s+(?:package|module|library|dependency|plugin|component|thing))\s+",
    re.IGNORECASE,
)


def _negated_meaning(target: str) -> PackageState:
    """What does "not <target>" say about the thing it is attached to?

        does not crash    negating a failure  -> healthy
        did not load      negating an action  -> failing
        is not working    negating a state    -> failing

    An unrecognised word yields UNKNOWN: something was negated and we do not
    know what it means.
    """
    lowered = re.sub(r"[\s-]+", " ", target.lower().strip())
    if lowered in FAILURE_VERBS:
        return PackageState.HEALTHY
    if lowered in POSITIVE_STATES or lowered in EXPECTED_ACTIONS:
        return PackageState.FAILING
    return PackageState.UNKNOWN


def predicate_window(
    text: str, start: int, end: int, other_spans: Sequence[tuple[int, int]]
) -> tuple[str, str]:
    """The text that belongs to *this* mention, before and after it.

    Stops at a sentence break and at any other package mention: a cue may only
    speak for the package it is attached to. Commas and conjunctions do not stop
    it, because `X starts but crashes` is one statement about X.
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

    sentence_breaks = [m.end() for m in SENTENCE_BREAK_RE.finditer(before)]
    if sentence_breaks:
        before = before[sentence_breaks[-1] :]

    first_break = SENTENCE_BREAK_RE.search(after)
    if first_break:
        after = after[: first_break.start()]

    return before, after


def _predicate_positions(after: str) -> list[tuple[int, str]]:
    """Offsets in ``after`` where a predicate about the mention may start."""
    positions = [(0, "")]
    for match in PREDICATE_LINK_RE.finditer(after):
        positions.append((match.end(), match.group(0).strip()))
    return positions


def _read_predicate(fragment: str) -> tuple[PackageState, str] | None:
    """Classify one anchored predicate, or None when it says nothing."""
    negated = NEGATED_AFTER_RE.match(fragment)
    if negated:
        return _negated_meaning(negated.group("verb")), negated.group(0).strip()

    cessation = CESSATION_RE.match(fragment)
    if cessation:
        return PackageState.FAILING, cessation.group(0).strip()

    health = HEALTH_AFTER_RE.match(fragment)
    if health:
        return PackageState.HEALTHY, health.group(0).strip()

    failure = FAILURE_VERB_AFTER_RE.match(fragment)
    if failure:
        return PackageState.FAILING, failure.group(0).strip()

    return None


def _read_predicate_with_subject(fragment: str) -> tuple[PackageState, str, bool] | None:
    """Read a predicate that may carry its own subject.

    Returns (state, cue, attributable). A coreference subject - `it`, `the
    package` - refers back to the mention, so the predicate is attributed to it.
    Any other subject means the clause is about something else; if that clause
    reports a failure we return it as *unattributable*, which makes the mention
    ambiguous rather than a safe dependency.
    """
    direct = _read_predicate(fragment)
    if direct is not None:
        state, cue = direct
        return state, cue, True

    coreference = COREFERENCE_RE.match(fragment)
    if coreference:
        inner = _read_predicate(fragment[coreference.end() :])
        if inner is not None:
            state, cue = inner
            return state, f"{coreference.group(0).strip()} {cue}", True
        return None

    # Some other subject. Only a failure matters here, and only as ambiguity.
    words = fragment.split()
    for index in range(1, min(len(words), 4)):
        tail = " ".join(words[index:])
        inner = _read_predicate(tail)
        if inner is not None and inner[0] is PackageState.FAILING:
            return inner[0], f"unattributed: {inner[1]}", False
    return None


def classify_mention(
    text: str,
    start: int,
    end: int,
    name: str,
    canonical: str,
    other_spans: Sequence[tuple[int, int]] = (),
) -> PackageMention:
    """Read a mention onto both axes.

    Relation and state are decided separately, so `depends on X, which is
    healthy` keeps both facts and `import X, but it crashes` is not silently
    filed as a plain dependency.
    """
    before, after = predicate_window(text, start, end, other_spans)

    dependency_cue = DEPENDENCY_CUE_RE.search(before)
    install_context = INSTALL_CONTEXT_RE.search(before)
    relation = (
        PackageRelation.DEPENDENCY
        if (dependency_cue or install_context)
        else PackageRelation.UNKNOWN
    )
    relation_match = dependency_cue or install_context
    relation_cue = relation_match.group(0).strip() if relation_match else ""

    states: list[tuple[PackageState, str]] = []
    ambiguous = False
    for offset, link in _predicate_positions(after):
        reading = _read_predicate_with_subject(after[offset:])
        if reading is None:
            continue
        state, cue, attributable = reading
        if attributable:
            states.append((state, f"{link} {cue}".strip()))
        elif state is PackageState.FAILING:
            ambiguous = True

    # A failure stated before the mention: `could not resolve X`, `error in X`.
    negated_before = NEGATED_BEFORE_RE.search(before)
    if negated_before:
        before_state = _negated_meaning(negated_before.group("verb"))
        states.append((before_state, negated_before.group(0).strip()))
        if before_state is PackageState.UNKNOWN:
            # `could not import X`: something was negated about X and we cannot
            # say what it means. That is not a licence to file X as a dependency.
            ambiguous = True
    elif FAILURE_LOCATION_BEFORE_RE.search(before):
        location = FAILURE_LOCATION_BEFORE_RE.search(before)
        assert location is not None
        states.append((PackageState.FAILING, location.group(0).strip()))
    elif HEALTH_BEFORE_RE.search(before):
        health = HEALTH_BEFORE_RE.search(before)
        assert health is not None
        states.append((PackageState.HEALTHY, health.group(0).strip()))

    distinct = {state for state, _ in states if state is not PackageState.UNKNOWN}
    if PackageState.FAILING in distinct and PackageState.HEALTHY in distinct:
        state = PackageState.CONFLICTED
    elif PackageState.FAILING in distinct:
        state = PackageState.FAILING
    elif PackageState.HEALTHY in distinct:
        state = PackageState.HEALTHY
    else:
        state = PackageState.UNKNOWN

    cue_parts = [relation_cue] if relation_cue else []
    cue_parts += [cue for _, cue in states]
    if ambiguous:
        cue_parts.append("unattributed failure nearby")

    return PackageMention(
        name=name,
        canonical=canonical,
        relation=relation,
        state=state,
        start=start,
        end=end,
        cue="; ".join(part for part in cue_parts if part),
        context=(before[-32:] + text[start:end] + after[:40]).strip(),
        ambiguous=ambiguous and state is PackageState.UNKNOWN,
    )


def aggregate_states(mentions: list[PackageMention]) -> list[PackageMention]:
    """Merge every mention of the same package into one consistent verdict.

    `@x is healthy.` and `@x crashes.` are two sentences about one package, and
    together they are a contradiction - not a health fact in one place and a
    blame in another. Aggregation is by canonical name, so repeats, later
    sentences and sub-path aliases all fold together.
    """
    by_package: dict[str, set[PackageState]] = {}
    for mention in mentions:
        by_package.setdefault(mention.canonical, set()).add(mention.state)

    merged: list[PackageMention] = []
    for mention in mentions:
        states = by_package[mention.canonical]
        if PackageState.FAILING in states and PackageState.HEALTHY in states:
            merged.append(
                PackageMention(
                    name=mention.name,
                    canonical=mention.canonical,
                    relation=mention.relation,
                    state=PackageState.CONFLICTED,
                    start=mention.start,
                    end=mention.end,
                    cue=f"{mention.cue}; contradicted by another mention of the same package",
                    context=mention.context,
                    ambiguous=mention.ambiguous,
                )
            )
            continue
        # A state stated anywhere about this package applies to every mention of
        # it: "@x is healthy ... @x again" must not leave the second one blank.
        resolved = mention.state
        if resolved is PackageState.UNKNOWN:
            known = states - {PackageState.UNKNOWN}
            if len(known) == 1:
                resolved = next(iter(known))
        if resolved is mention.state:
            merged.append(mention)
        else:
            merged.append(
                PackageMention(
                    name=mention.name,
                    canonical=mention.canonical,
                    relation=mention.relation,
                    state=resolved,
                    start=mention.start,
                    end=mention.end,
                    cue=f"{mention.cue}; stated elsewhere in this report".strip("; "),
                    context=mention.context,
                    ambiguous=mention.ambiguous,
                )
            )
    return merged


def _package_aliases(name: str) -> list[str]:
    """`@scope/pkg/file.js` also stands for `@scope/pkg`."""
    aliases = [name]
    if name.startswith("@") and name.count("/") >= 2:
        scope, _, rest = name.partition("/")
        aliases.append(f"{scope}/{rest.split('/')[0]}")
    return aliases


# --- state assertions -------------------------------------------------------
#
# A report makes claims about condition: "it crashes", "this package is
# healthy", "the server fell over". Each such claim has a subject, and the
# subject decides whether the claim can touch a package at all. Reading claims
# only inside a package's own window loses every claim that lives in the next
# sentence, which is how `We import X! It crashes.` kept X filed as a harmless
# dependency.

CLAUSE_SPLIT_RE = re.compile(
    # The backtick is a boundary: `Diagnostic summary says `It is operational`.`
    # is two clauses, and reading it as one buried the claim inside a subject.
    r"(?:[.!?](?=\s|$)|[\n;:`]|,\s+|\s+(?:and|but|then|yet|however|while|so)\s+)",
    re.IGNORECASE,
)

# The pronouns of ANAPHOR_RE on their own, for when the subject head has to be
# found by the predicate behind it rather than by the start of the clause.
ANAPHOR_WORD_RE = re.compile(r"(?:it|they|these|those|them|itself|themselves)", re.IGNORECASE)

# Subjects that point back at something already named.
ANAPHOR_RE = re.compile(
    r"^\s*(?:it|they|these|those"
    r"|(?:this|that|the)\s+(?:package|module|library|dependency|plugin|component|thing|one))"
    r"\b",
    re.IGNORECASE,
)

# The same nouns ANAPHOR_RE uses, but with the slot in front of them parsed as
# determiner-plus-modifiers instead of matched against three fixed words. That
# is what separates `said package`, `the same package` and `this exact module`
# from `the boot graph`: the head noun decides, not the determiner. No noun is
# added here - extending the list was declined, and would not have fixed the
# shape of the defect.
PACKAGE_NOUN_RE = re.compile(
    r"packages?|modules?|librar(?:y|ies)|dependenc(?:y|ies)|plugins?|components?",
    re.IGNORECASE,
)

# A subject that opens with a package-shaped token: `@dsh-client-modules is
# operational`. The token need not resolve to anything we know - an unknown
# package is still a package, and a claim about one is still a claim about a
# package.
SUBJECT_PACKAGE_TOKEN_RE = re.compile(r"^@[\w.-]+(?:/[\w.-]+)?\b")


# A subject that names something other than a package: "the server", "the host
# process", "the build". These claims are bound elsewhere and are not our
# business - but they are bound, so they are not unresolved either.
OTHER_SUBJECT_RE = re.compile(r"^\s*(?:the|our|my|its|his|her|their|a|an)?\s*[a-z][\w-]+", re.I)

# The positive-state words as whole words, compiled once.
POSITIVE_WORD_RE = re.compile(rf"\b(?:{_POSITIVE_ALTERNATIVES})\b", re.IGNORECASE)

# The trailing token is a word boundary. It was once a literal \x08,
# which matched nothing, so every bare adjective read as a copula.
COPULA_RE = re.compile(r"(?:is|was|are|were|looks?|seems?|remains?)\b", re.IGNORECASE)

# Wrappers a report puts around a sentence: quotes, brackets, bullets, quoting
# markers. They are punctuation, not subjects.
SUBJECT_WRAPPER_RE = re.compile(r"^[\s\"'`(\[<>*_~#-]+|[\s\"'`)\]>*_~]+$")

# A subject that points at some *other* named thing we can actually see in the
# text: another package, a path, a file, a builtin. Anything else - a bare noun
# phrase we cannot resolve - is unresolved, not somebody else's problem.
ENTITY_IN_SUBJECT_RE = re.compile(r"@[\w.-]+/[\w.-]+|node:[\w/]+|[\w.-]+/[\w.-]+|\.\w{2,4}\b")

# Real code, recognised by structure rather than by a single bracket: fenced
# blocks, inline spans, stack frames, and lines whose punctuation density leaves
# no room for prose. A parenthesis is not evidence of code - `(runtime
# dependency)` and `[checked]` are ordinary things to write in a bug report, and
# treating them as code once deleted an explicit health statement.
CODE_BLOCK_RE = re.compile(r"```|~~~")
INLINE_SPAN_RE = re.compile(r"`([^`\n]+)`")
STACK_FRAME_RE = re.compile(
    # `at` needs a frame behind it - a qualified name, a path, a call - or
    # `At startup it is operational` reads as a stack trace and its claim is
    # thrown away.
    r"^\s*(?:at\s+\S*[.:/(]\S*|File\s+\"[^\"]+\",\s*line\s+\d+|[\w./-]+\.(?:ts|js|py|go|rs):\d+)",
    re.IGNORECASE | re.MULTILINE,
)
CODE_PUNCTUATION_RE = re.compile(r"[{}=<>|&;]|=>|::|\bfunction\b|\breturn\b|\bconst\b")

# A statement about how packages are wired together, not about their condition.
RELATION_STATEMENT_RE = re.compile(
    r"\b(?:imports?|importing|requires?|requiring|depends?\s+on|uses?|using|installs?"
    r"|installing|bundles?|is\s+(?:a|one\s+of\s+our)\s+dependenc(?:y|ies))\b",
    re.IGNORECASE,
)


def _strip_wrapper(text: str) -> str:
    """Remove quoting, bullets and brackets from around a subject."""
    stripped = text.strip()
    previous = None
    while stripped and stripped != previous:
        previous = stripped
        stripped = SUBJECT_WRAPPER_RE.sub("", stripped).strip()
    return stripped


# A clause that has a subject and a verb-ish word asserts *something*, even when
# we cannot say what. This is deliberately shallow: it does not try to know the
# verb, only that the sentence is making a statement.
_CLAIM_SHAPE_RE = re.compile(
    r"^[\s\"'`(\[<>*_~#-]*[\w@./-]+(?:\s+[\w@./-]+){0,3}\s+[a-z]+(?:s|ed|ing)?\b",
    re.IGNORECASE,
)


def _looks_like_a_claim(clause: str) -> bool:
    """Does this clause state something about its subject?"""
    stripped = clause.strip()
    if len(stripped.split()) < 2:
        return False
    return bool(_CLAIM_SHAPE_RE.match(stripped))


def _subject_prefix(clause: str) -> str:
    """The first few words of a clause: its subject, roughly."""
    words = clause.strip().split()
    return " ".join(words[:4])


# The relative pronouns. A finite verb behind one of these belongs to the
# relative clause, not to the sentence.
RELATIVE_PRONOUN_RE = re.compile(r"\b(?:that|which|who|whom|whose)\b", re.IGNORECASE)

# Punctuation to strip from a token before asking what shape it is.
STRIP_CHARS = ".,;:!?()[]\"'"

# Words that open a subordinate clause. Whatever verb follows one, it is not
# the main predicate.
SUBORDINATOR_RE = re.compile(
    r"\b(?:when|whenever|while|after|before|because|since|if|unless|although|though"
    r"|whereas|until|once|as)\b",
    re.IGNORECASE,
)

# A word that could be a predicate at all: letters, then whatever punctuation
# ends the clause. Never another path, scope or file name.
PREDICATE_SHAPE_RE = re.compile(r"^[A-Za-z]+[^\w/@]*$")


def _verb_shaped(word: str) -> bool:
    """Does this word stand where a predicate stands?

    Morphology and the closed auxiliary class decide, so no list of verbs has
    to be kept. `is`, `did`, `remains`, `passed`, `crashes` do; `version`,
    `startup` and `plugins/react` do not.
    """
    if PREDICATE_SHAPE_RE.match(word) is None:
        return False
    bare = word.rstrip(".,;:!?)]\"'")
    return bool(AUXILIARY_RE.match(bare) or INFLECTED_RE.match(bare))


# The closed class of English auxiliaries, plus inflection. Together these say
# whether a word stands where a predicate stands - `is`, `did`, `remains`,
# `passed`, `crashes` - without listing predicates. `version`, in
# `@x version 0.1.2-alpha.1`, does not, and reading it as one made an ordinary
# environment line block every action.
AUXILIARY_RE = re.compile(
    r"^(?:is|was|are|were|be|been|being|am|has|have|had|do|does|did"
    r"|can|could|will|would|shall|should|may|might|must)$",
    re.IGNORECASE,
)
INFLECTED_RE = re.compile(r"^[a-z]+(?:s|ed|ing)$", re.IGNORECASE)

# A copula anywhere in a span, as a whole word. COPULA_RE is anchored where it
# is used and would find `is` inside `this`.
COPULA_WORD_RE = re.compile(
    r"\b(?:is|was|are|were|be|been|being|am|looks?|seems?|remains?)\b", re.I
)

# Words, as opposed to the punctuation a clause ends on.
WORD_RE = re.compile(r"\w+")


def _clause_is_code(clause: str) -> bool:
    """Is this clause code or log output rather than someone writing a sentence?

    Structure decides, not a single character. Brackets appear constantly in
    ordinary reports - `(runtime dependency)`, `[checked]` - and skipping a
    clause because it contains one deleted explicit health statements.
    """
    if CODE_BLOCK_RE.search(clause) or STACK_FRAME_RE.search(clause):
        return True
    # An inline span is a quotation, and what is quoted can be a sentence:
    # "Diagnostic summary: `It is operational`" states a condition. Treating the
    # backticks as code hid that claim while the same region was still allowed
    # to contribute paths and symbols towards identity - evidence counted in one
    # direction only.
    body = INLINE_SPAN_RE.sub(r"\1", clause)
    words = [w for w in body.split() if w]
    if not words:
        return True
    punctuation = len(CODE_PUNCTUATION_RE.findall(body))
    return punctuation >= 2 and punctuation / len(words) > 0.3


def _predicate_follows(clause: str, at: int) -> bool:
    """Is there a word standing where a predicate stands, after position `at`?

    `@types/react bigint/ReactNode)` names a package and then names another
    path: an enumeration inside a bug title, with nothing predicated of
    anything. `@x is operational` has `is`. Without this a title that merely
    lists packages counted as a claim about the first one.
    """
    return any(_verb_shaped(word) for word in clause[at:].split())


def _subject_is_a_package_token(clause: str) -> bool:
    """Does this clause open with a package token and then say something about it?

    `@dsh-client-modules is operational` does. `@types/react bigint/ReactNode)`
    - the tail of a bug title listing what failed - does not: a package token
    followed by another path-shaped token is an enumeration, not a subject with
    a predicate behind it.
    """
    text = clause.strip()
    match = SUBJECT_PACKAGE_TOKEN_RE.match(text)
    if match is None:
        return False
    rest = text[match.end() :].split()
    return bool(rest) and _verb_shaped(rest[0])


def _subject_refers_back(clause: str) -> bool:
    """Does this clause's subject refer back to a package?

    Either as a pronoun or as a package-kind noun, and in both cases found by
    the predicate standing behind it rather than by the start of the clause.
    `At startup it is operational` puts a fronted adverbial in front of the
    pronoun; anchoring the test at position zero read that as prose about
    nothing and let the claim go.
    """
    words = clause.split()
    for index, word in enumerate(words[:-1]):
        if not _verb_shaped(words[index + 1]):
            continue
        head = word.strip(".,;:!?()[]\"'")
        if ANAPHOR_WORD_RE.fullmatch(head) or PACKAGE_NOUN_RE.fullmatch(head):
            return True
    return False


def _subject_names_a_package_kind(clause: str) -> bool:
    """Does this clause's subject refer to a package by its head noun?

    A package-kind noun with a predicate directly behind it is a subject:
    `said package is`, `the same package passed`, `this carefully audited
    bundled runtime component remains`. How many modifiers stand in front of it
    does not matter - counting them was a window, and a report only had to
    write one more adjective to fall outside it. `while resolving a module` has
    no predicate behind the noun, and `plugin-registered commands` never had a
    package noun at all.
    """
    words = clause.split()
    for index, word in enumerate(words[:-1]):
        if PACKAGE_NOUN_RE.fullmatch(word.strip(".,;:!?()[]\"'")) and _verb_shaped(
            words[index + 1]
        ):
            return True
    return False


def _is_relation_statement(clause: str) -> bool:
    """Is *this clause* about wiring rather than condition?

    `The project requires @x` is. `It has no issues when using plugins` is not:
    its main predicate is `has no issues`, and `using` only opens a subordinate
    clause. Searching the whole clause let any trailing `using`/`requiring`/
    `importing` delete the claim standing in front of it, so the relation verb
    now has to sit where a main predicate sits - in the head segment, close
    behind the subject.
    """
    head = SUBORDINATOR_RE.split(clause, maxsplit=1)[0]
    match = RELATION_STATEMENT_RE.search(head)
    if match is None:
        return False
    before, after = head[: match.start()], head[match.end() :]

    # `The package using our fallback ...`: a bare participle standing after a
    # noun opens a reduced relative clause. It can only be a main predicate
    # with an auxiliary in front of it - `we are using @x` - and without one it
    # is describing the noun, not predicating anything of it.
    verb = match.group(0).strip().lower()
    if verb.endswith("ing"):
        preceding = before.split()
        if not preceding or not AUXILIARY_RE.match(preceding[-1].strip(STRIP_CHARS)):
            return False
        # `we are using @x`: the auxiliary belongs to the relation verb, so it
        # is not something else being predicated.
        before = " ".join(preceding[:-1])

    # `The package that uses our fallback ...`: an explicit relative pronoun
    # says the same thing about a finite verb.
    if RELATIVE_PRONOUN_RE.search(before):
        return False

    # And whatever the form, if something else in the clause stands where a
    # predicate stands, the relation verb was not the main one. `passed`,
    # `survived`, `behaved` and `ran` all do; only checking for a copula here
    # let every one of them through.
    return not any(_verb_shaped(word) for word in (before + " " + after).split())


BINDING_EXPLICIT = "explicit"
BINDING_ANAPHORIC = "anaphoric"
BINDING_OTHER = "other_subject"
BINDING_UNRESOLVED = "unresolved"
#: A claim we could see but could not read, whose subject *points at* a
#: package: "It is operational". The report is saying something about that
#: package in words we do not understand, so nothing about it is actionable.
BINDING_UNINTERPRETED = "uninterpreted"
#: The same, but the subject is ordinary prose that merely follows a mention:
#: "dsh web starts". Weak - it may not be about the package at all - so it
#: only counts where nothing else establishes identity.
BINDING_UNINTERPRETED_WEAK = "uninterpreted_weak"

#: Where an unread claim's target came from. Strength follows the *source*, not
#: the wording of the subject. Deciding it by "does the subject match the
#: pronoun list" left a clause that spells the package out - `@dsh-client-modules
#: is operational` - weaker than one that only points at it.
#:
#: The clause names a package, or its subject is a package-shaped token.
SOURCE_EXPLICIT_PACKAGE = "explicit_package"
#: The subject refers back to a package: a pronoun, or a noun phrase whose head
#: noun names a package-kind.
SOURCE_RESOLVED_ANAPHOR = "resolved_anaphor"
#: Ordinary prose that happens to follow a mention. The nearest package is a
#: guess, and the claim may not be about a package at all.
SOURCE_PROXIMITY_GUESS = "proximity_guess"


@dataclass(frozen=True)
class StateAssertion:
    """One claim about condition, and what it is attached to."""

    state: PackageState
    binding: str
    package: str | None
    subject_text: str
    cue: str
    start: int
    #: For unread claims, where the target came from: SOURCE_EXPLICIT_PACKAGE,
    #: SOURCE_RESOLVED_ANAPHOR or SOURCE_PROXIMITY_GUESS. Empty for claims we
    #: could read, where the binding already says how they attached.
    source: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "state": str(self.state),
            "binding": self.binding,
            "package": self.package,
            "subject": self.subject_text[:40],
            "cue": self.cue[:60],
            "source": self.source,
        }


FENCE_RE = re.compile(r"^[ \t]*(?:```|~~~).*$", re.MULTILINE)


def quoted_spans(text: str) -> list[tuple[int, int]]:
    """The regions a report has fenced off as quotation.

    A fenced block is material the reporter is showing, not asserting: a
    documentation example, someone else's output, a snippet from a manual.
    Nothing inside one says who is failing or what condition anything is in.
    Reading fenced text as the reporter's own claim let a block introduced as
    `Documentation example only:` make its package a primary subject, which
    cancelled the conflict with the package the report actually blamed.

    Mechanical strings - paths, symbols, error text - are still taken from
    inside a fence. Those are not claims about authorship; a pasted trace
    evidences what the machine printed whoever pasted it.
    """
    marks = list(FENCE_RE.finditer(text))
    spans = [(marks[i].end(), marks[i + 1].start()) for i in range(0, len(marks) - 1, 2)]
    if len(marks) % 2:
        spans.append((marks[-1].end(), len(text)))
    return spans


def blank_quoted(text: str) -> str:
    """The same text with fenced regions replaced by spaces.

    Offsets are preserved, so anything measured on this string lines up with
    the original. Extracting twice - once with quotations, once without - is
    what separates a path this reporter is describing from one they pasted out
    of somebody else's ticket.
    """
    blanked = list(text)
    for start, end in quoted_spans(text):
        for index in range(start, min(end, len(blanked))):
            if blanked[index] != "\n":
                blanked[index] = " "
    return "".join(blanked)


def _inside(position: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _clauses(text: str) -> list[tuple[int, str]]:
    """(offset, clause) pairs. Sentence ends and coordinators both split."""
    clauses: list[tuple[int, str]] = []
    position = 0
    for match in CLAUSE_SPLIT_RE.finditer(text):
        clause = text[position : match.start()]
        if clause.strip():
            clauses.append((position, clause))
        position = match.end()
    tail = text[position:]
    if tail.strip():
        clauses.append((position, tail))
    return clauses


def _state_in_clause(clause: str) -> tuple[PackageState, str, int, bool] | None:
    """What this clause asserts, where it starts, and whether it is adjectival.

    A predicate describes the clause's subject; an adjective describes the noun
    it stands in front of. Binding them the same way makes
    `it imports the healthy @scope/lib` claim that *it* is healthy.
    """
    for offset in range(len(clause)):
        if offset and not clause[offset - 1].isspace():
            continue
        reading = _read_predicate(clause[offset:])
        if reading is not None:
            return reading[0], reading[1], offset, False

    # A positive state used adjectivally: "as a healthy dependency". The word
    # boundaries are load-bearing: without them `fine` matches inside
    # `undefined` and `ok` inside `maxTokens`, turning source code into health
    # claims about whatever package the snippet happens to mention.
    adjective = re.search(POSITIVE_WORD_RE, clause)
    if adjective:
        return PackageState.HEALTHY, adjective.group(0), adjective.start(), True
    return None


def extract_state_assertions(
    text: str,
    mention_spans: list[tuple[int, int, str]],
    quoted: Sequence[tuple[int, int]] = (),
) -> list[StateAssertion]:
    """Bind every condition claim to a package, to another subject, or to nothing.

    Anaphora binds only when the antecedent is unique: if two different packages
    were named before `it crashes`, we do not guess which one crashed, and the
    claim is recorded as unresolved so the gate can refuse.
    """
    assertions: list[StateAssertion] = []
    last_subject_package: str | None = None

    for offset, clause in _clauses(text):
        if _inside(offset, quoted):
            # Quoted material. It asserts nothing, in either direction.
            continue
        clause_end = offset + len(clause)
        inside = [m for m in mention_spans if offset <= m[0] < clause_end]

        # Read the claim first. Deciding "this looks like code" before parsing is
        # how `It is healthy (verified)` lost its health statement to a pair of
        # brackets.
        found = _state_in_clause(clause)
        if found is not None and found[0] is PackageState.UNKNOWN:
            # We saw a predicate and could not say what it asserts: `it did not
            # malfunction`, `it wasn't defective`. Dropping it here read as
            # silence, which is the whole defect the unread-claim path exists
            # to prevent - so it goes down that path like any other clause we
            # could not read.
            found = None

        if found is None:
            # No readable predicate. If this clause is prose *about* a package -
            # named here, or referred to by a subject we would have resolved -
            # then it asserted something we could not read. Recording that is
            # what stops the next unknown verb ("It malfunctions", "It becomes
            # unusable") from reading as silence.
            if inside:
                last_subject_package = inside[-1][2]
            if (
                not _clause_is_code(clause)
                and not _is_relation_statement(clause)
                and _looks_like_a_claim(clause)
            ):
                subject = _strip_wrapper(_subject_prefix(clause))
                target = None
                # How firmly the claim is attached is a question about its
                # subject, and it is settled before we go looking for a nearby
                # package to pin it on. Reading it off the pronoun list instead
                # made `@dsh-client-modules is operational` - which names the
                # package outright - weaker than `it is operational`.
                if inside and _predicate_follows(clause, inside[0][1] - offset):
                    target = inside[0][2]
                    source = SOURCE_EXPLICIT_PACKAGE
                elif _subject_is_a_package_token(clause):
                    source = SOURCE_EXPLICIT_PACKAGE
                elif ANAPHOR_RE.match(subject) or _subject_refers_back(clause):
                    source = SOURCE_RESOLVED_ANAPHOR
                else:
                    source = SOURCE_PROXIMITY_GUESS
                    if inside:
                        target = inside[0][2]
                if target is None and subject and not ENTITY_IN_SUBJECT_RE.search(subject):
                    antecedents = {m[2] for m in mention_spans if m[0] < offset}
                    target = next(iter(antecedents)) if len(antecedents) == 1 else None
                if target is not None or subject:
                    binding = (
                        BINDING_UNINTERPRETED_WEAK
                        if source == SOURCE_PROXIMITY_GUESS
                        else BINDING_UNINTERPRETED
                    )
                    assertions.append(
                        StateAssertion(
                            PackageState.UNKNOWN,
                            binding,
                            target,
                            subject,
                            clause.strip()[:60],
                            offset,
                            source,
                        )
                    )
            continue
        state, cue, predicate_at, adjectival = found
        subject_text = _strip_wrapper(clause[:predicate_at])

        # A bare positive word with a package standing right after it is a
        # modifier, not a predicate: `imports the healthy @scope/lib` says
        # @scope/lib is healthy, not that the importer is.
        if (
            not adjectival
            and state is PackageState.HEALTHY
            and not COPULA_RE.match(cue)
            and any(m[0] - offset >= predicate_at for m in inside)
        ):
            adjectival = True

        if adjectival:
            # "it imports the healthy @scope/lib": the adjective describes the
            # package standing after it, not whoever the clause is about.
            modified = [m for m in inside if m[0] - offset >= predicate_at]
            if modified:
                assertions.append(
                    StateAssertion(
                        state, BINDING_EXPLICIT, modified[0][2], "(adjective)", cue, offset
                    )
                )
                continue

        # A package named in this clause owns the claim.
        preceding = [m for m in inside if m[0] - offset < predicate_at]
        if preceding:
            package = preceding[-1][2]
            last_subject_package = package
            assertions.append(
                StateAssertion(state, BINDING_EXPLICIT, package, subject_text, cue, offset)
            )
            continue

        if not subject_text:
            # Coordinated onto the previous clause: same subject as before. With
            # no previous package subject this is a bare fragment - a log line,
            # a stack frame - which asserts nothing *about a package*, so it is
            # dropped rather than recorded as a dangling claim.
            if last_subject_package:
                assertions.append(
                    StateAssertion(
                        state, BINDING_EXPLICIT, last_subject_package, "(coordinated)", cue, offset
                    )
                )
            continue

        # A subject that refers back to a package - a pronoun, or a noun phrase
        # whose head noun names a package-kind. The same test the unread path
        # uses: reading a claim we understand should not attach it more weakly
        # than one we do not, and checking only the pronoun list here let
        # `Said package is healthy` retract a failure and disappear.
        refers_back = bool(ANAPHOR_RE.match(subject_text)) or _subject_refers_back(clause)
        if not refers_back:
            # A noun phrase naming nothing we can see: `the server`, `the host
            # process`. We will not attribute it to the nearest package - that
            # would blame something the report never blamed - and we will not
            # wave it through as somebody else's problem either. It dangles,
            # and the gate decides what that costs.
            assertions.append(
                StateAssertion(state, BINDING_UNRESOLVED, None, subject_text, cue, offset)
            )
            last_subject_package = None
            continue

        # Bind it only when exactly one antecedent exists.
        antecedents = {m[2] for m in mention_spans if m[0] < offset}
        if len(antecedents) == 1:
            package = next(iter(antecedents))
            last_subject_package = package
            assertions.append(
                StateAssertion(
                    state,
                    BINDING_ANAPHORIC,
                    package,
                    subject_text,
                    cue,
                    offset,
                    SOURCE_RESOLVED_ANAPHOR,
                )
            )
        else:
            # Zero antecedents, or several: refuse to guess. The claim still
            # points at *a* package, and the source says so, so the gate can
            # refuse rather than treat it as somebody else's business.
            assertions.append(
                StateAssertion(
                    state,
                    BINDING_UNRESOLVED,
                    None,
                    subject_text,
                    cue,
                    offset,
                    SOURCE_RESOLVED_ANAPHOR,
                )
            )
        continue

    return assertions


def apply_assertions(
    mentions: list[PackageMention], assertions: list[StateAssertion]
) -> list[PackageMention]:
    """Fold bound assertions into the mentions of the packages they name."""
    states: dict[str, set[PackageState]] = {}
    for assertion in assertions:
        if assertion.package and assertion.state is not PackageState.UNKNOWN:
            states.setdefault(assertion.package, set()).add(assertion.state)

    updated: list[PackageMention] = []
    for mention in mentions:
        extra = states.get(mention.canonical, set())
        combined = extra | ({mention.state} - {PackageState.UNKNOWN})
        if PackageState.FAILING in combined and PackageState.HEALTHY in combined:
            state = PackageState.CONFLICTED
        elif PackageState.CONFLICTED in combined:
            state = PackageState.CONFLICTED
        elif PackageState.FAILING in combined:
            state = PackageState.FAILING
        elif PackageState.HEALTHY in combined:
            state = PackageState.HEALTHY
        else:
            state = PackageState.UNKNOWN
        if state is mention.state:
            updated.append(mention)
        else:
            updated.append(
                PackageMention(
                    name=mention.name,
                    canonical=mention.canonical,
                    relation=mention.relation,
                    state=state,
                    start=mention.start,
                    end=mention.end,
                    cue=f"{mention.cue}; bound from elsewhere in the report".strip("; "),
                    context=mention.context,
                    ambiguous=mention.ambiguous,
                )
            )
    return updated


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
        canonical = _package_aliases(match.group(0))[-1]
        mention = classify_mention(
            lowered, match.start(), match.end(), match.group(0), canonical, others
        )
        for alias in _package_aliases(mention.name):
            if (alias, match.start()) in seen:
                continue
            seen.add((alias, match.start()))
            subjects.package_mentions.append(
                PackageMention(
                    name=alias,
                    canonical=canonical,
                    relation=mention.relation,
                    state=mention.state,
                    start=mention.start,
                    end=mention.end,
                    cue=mention.cue,
                    context=mention.context,
                    ambiguous=mention.ambiguous,
                )
            )

    for match in BARE_PACKAGE_RE.finditer(lowered):
        name = match.group(1)
        if name.startswith("node:") or any(name == m.name for m in subjects.package_mentions):
            continue
        others = [s for s in spans if s != (match.start(1), match.end(1))]
        mention = classify_mention(lowered, match.start(1), match.end(1), name, name, others)
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

    # A package named inside a fenced block is being shown, not reported. It
    # keeps contributing paths and symbols above; what it does not do is become
    # a subject of this report or carry a condition.
    quoted = quoted_spans(lowered)
    subjects.package_mentions = [
        m for m in subjects.package_mentions if not _inside(m.start, quoted)
    ]

    # Claims made anywhere in the report, bound to whatever they are about.
    named_spans = [(m.start, m.end, m.canonical) for m in subjects.package_mentions]
    subjects.state_assertions = extract_state_assertions(lowered, named_spans, quoted)
    subjects.package_mentions = apply_assertions(
        subjects.package_mentions, subjects.state_assertions
    )
    subjects.package_mentions = aggregate_states(subjects.package_mentions)

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
