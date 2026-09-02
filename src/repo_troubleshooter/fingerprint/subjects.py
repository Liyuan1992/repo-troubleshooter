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
FEATURE_EXTRACTOR_VERSION = 10

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

SCOPED_PACKAGE_RE = re.compile(r"@[\w.-]+/[\w.-]+(?:/[\w.-]+)*")
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
    r"^(?:it|they|this|that|which|"
    r"the\s+(?:package|module|library|dependency|plugin|component|thing))\s+",
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
