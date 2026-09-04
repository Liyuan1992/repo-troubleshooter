"""Error fingerprinting.

Conservative on purpose. The job is to strip what varies between two machines
hitting the *same* problem (paths, line numbers, ports, ids, timestamps) while
keeping every token that could distinguish two *different* problems.

The failure mode we design against is over-normalisation: if
``PostgreSQL startup failed: connection refused`` and
``dsh web starts but __DSH_BOOT__ has zero entries`` collapse toward each other,
the retrieval layer will confidently answer the wrong question. So the
fingerprint also exposes ``discriminative`` tokens - the specific, structural
ones - and matching is gated on those rather than on overall text similarity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# --- volatile fragments: removed, in this order -----------------------------

_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # ISO timestamps and clock times
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), " "),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b"), " "),
    # UUIDs
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), " "),
    # memory addresses / long hex blobs
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), " "),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), " "),
    # Windows and POSIX absolute paths (keep the final component: it is often a module)
    (re.compile(r"\b[A-Za-z]:\\(?:[^\s:*?\"<>|\\]+\\)*"), " "),
    (re.compile(r"(?<![\w@.-])/(?:[\w.-]+/){2,}"), " "),
    # file:// URLs
    (re.compile(r"\bfile://\S*"), " "),
    # host:port and bare IPs
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), " "),
    (re.compile(r"\blocalhost:\d+\b", re.I), " localhost "),
    # :line:col suffixes
    (re.compile(r":\d+:\d+\b"), " "),
    (re.compile(r"\bline \d+\b", re.I), " "),
    # pids / process and temp ids
    (re.compile(r"\bpid[= ]\d+\b", re.I), " "),
    (re.compile(r"\btmp[\w-]{6,}\b", re.I), " "),
)

# --- token shapes we keep -----------------------------------------------------

EXCEPTION_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning))\b")
ERROR_CODE_RE = re.compile(r"\b(E[A-Z]{3,}|ERR_[A-Z0-9_]+|[A-Z]{2,}_[A-Z0-9_]{2,})\b")
BACKTICK_RE = re.compile(r"`([^`\n]{1,80})`")
PACKAGE_RE = re.compile(r"(@[\w.-]+/[\w.-]+(?:/[\w.-]+)*|\bnode:[\w/]+)")
DUNDER_RE = re.compile(r"\b(__[A-Z][A-Z0-9_]*__)\b")
DOTTED_RE = re.compile(r"\b([a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)+)\b")
CAMEL_RE = re.compile(r"\b([a-z]+(?:[A-Z][a-z0-9]+)+)\b")
HYPHEN_MODULE_RE = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+){1,4})\b")
# HTTP/API routes are structural names too. `/metrics` distinguishes an
# endpoint regression even when there is no exception type or source path.
HTTP_ROUTE_RE = re.compile(r"(?<!\w)(/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_@.:-]+)*)\b")
FRAME_RE = re.compile(r"^\s*at\s+([\w$.<>]+)", re.MULTILINE)
FILE_RE = re.compile(r"\b([\w.-]+\.(?:ts|tsx|js|mjs|cjs|py|json|yaml|yml|toml))\b")
VERSIONISH_RE = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[-.][\w.]+)?\b")

# Words that are technically identifiers but discriminate nothing on their own.
GENERIC_TOKENS = frozenset(
    {
        "error",
        "errors",
        "failed",
        "failure",
        "failing",
        "exception",
        "startup",
        "start",
        "starts",
        "started",
        "starting",
        "config",
        "configuration",
        "plugin",
        "plugins",
        "windows",
        "linux",
        "macos",
        "darwin",
        "win32",
        "node",
        "nodejs",
        "npm",
        "pnpm",
        "yarn",
        "server",
        "client",
        "service",
        "services",
        "module",
        "modules",
        "package",
        "packages",
        "version",
        "versions",
        "install",
        "installed",
        "build",
        "builds",
        "run",
        "running",
        "test",
        "tests",
        "true",
        "false",
        "null",
        "undefined",
        "warning",
        "info",
        "debug",
        "trace",
        "stack",
        "code",
        "type",
        "types",
        "value",
        "values",
        "object",
        "function",
        "string",
        "number",
        "array",
        "file",
        "files",
        "path",
        "paths",
        "line",
        "column",
        "port",
        "host",
        "url",
        "http",
        "https",
        "request",
        "response",
        "timeout",
        "user",
        "users",
        "system",
        "process",
        "command",
        "commands",
        "output",
        "input",
        "log",
        "logs",
        "issue",
        "problem",
        "not-found",
        "no-such-file",
    }
)

_WORD_RE = re.compile(r"[A-Za-z_$][\w$.-]{1,}")


@dataclass(frozen=True)
class ErrorFingerprint:
    raw: str
    signature: str
    signature_hash: str
    tokens: tuple[str, ...] = ()
    discriminative: tuple[str, ...] = ()
    exception_types: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    frames: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not self.signature.strip()

    def overlap(self, other_tokens: set[str]) -> set[str]:
        """Discriminative tokens shared with a candidate document."""
        return {t for t in self.discriminative if t in other_tokens}

    def to_json(self) -> dict[str, object]:
        return {
            "signature": self.signature,
            "signature_hash": self.signature_hash,
            "discriminative": list(self.discriminative),
            "exception_types": list(self.exception_types),
            "error_codes": list(self.error_codes),
            "symbols": list(self.symbols),
            "packages": list(self.packages),
            "frames": list(self.frames),
            "files": list(self.files),
            "versions": list(self.versions),
        }


def normalize(text: str) -> str:
    """Remove machine-specific noise, keep everything that identifies the fault."""
    out = text
    for pattern, replacement in _SUBSTITUTIONS:
        out = pattern.sub(replacement, out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{2,}", "\n", out)
    return out.strip()


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return tuple(ordered)


def _is_discriminative(token: str) -> bool:
    lowered = token.lower().strip(".-_")
    if not lowered or lowered in GENERIC_TOKENS:
        return False
    if len(lowered) < 4:
        return False
    if lowered.isdigit():
        return False
    # structural shapes carry identity
    if any(ch in token for ch in "@/_."):
        return True
    if "-" in lowered:
        return True
    if CAMEL_RE.fullmatch(token) or EXCEPTION_RE.fullmatch(token):
        return True
    if token.isupper() and len(token) >= 4:
        return True
    # a plain lowercase word is discriminative only when it is not a common one
    return len(lowered) >= 6 and lowered not in GENERIC_TOKENS


def tokenize(text: str) -> set[str]:
    """Lowercased token set used for candidate-side overlap checks."""
    tokens: set[str] = set()
    for match in _WORD_RE.finditer(text):
        raw = match.group(0).strip(".-_")
        if not raw:
            continue
        tokens.add(raw.lower())
        # a/b.c also contributes its parts, so `e.indexOf` matches `indexOf`
        for part in re.split(r"[./@]", raw):
            part = part.strip("-_")
            if len(part) >= 3:
                tokens.add(part.lower())
    for match in DUNDER_RE.finditer(text):
        tokens.add(match.group(1).lower())
    return tokens


def fingerprint(raw_error: str | None, *, extra_context: str | None = None) -> ErrorFingerprint:
    """Build a fingerprint from an error string (plus optional free-text symptom)."""
    source = "\n".join(part for part in (raw_error, extra_context) if part)
    if not source.strip():
        return ErrorFingerprint(raw="", signature="", signature_hash="")

    signature = normalize(source)

    exceptions = _dedupe(EXCEPTION_RE.findall(signature))
    codes = _dedupe([c for c in ERROR_CODE_RE.findall(signature) if c not in exceptions])
    packages = _dedupe([m[0] if isinstance(m, tuple) else m for m in PACKAGE_RE.findall(signature)])
    frames = _dedupe(FRAME_RE.findall(signature))
    files = _dedupe(FILE_RE.findall(signature))
    versions = _dedupe(VERSIONISH_RE.findall(signature))

    symbols: list[str] = []
    symbols += [s.strip() for s in BACKTICK_RE.findall(signature)]
    symbols += DUNDER_RE.findall(signature)
    symbols += DOTTED_RE.findall(signature)
    symbols += CAMEL_RE.findall(signature)
    symbols += HYPHEN_MODULE_RE.findall(signature)
    symbols += HTTP_ROUTE_RE.findall(signature)
    symbols_t = _dedupe([s for s in symbols if s and not s.isdigit()])

    candidates: list[str] = [
        *exceptions,
        *codes,
        *packages,
        *symbols_t,
        *frames,
        *files,
    ]
    # plain words that are specific enough to matter (e.g. "postgresql", "preload")
    candidates += [w for w in re.findall(r"\b[A-Za-z][A-Za-z0-9]{5,}\b", signature)]

    discriminative = _dedupe([c.lower() for c in candidates if _is_discriminative(c)])

    return ErrorFingerprint(
        raw=source,
        signature=signature,
        signature_hash=hashlib.sha256(signature.lower().encode("utf-8")).hexdigest()[:32],
        tokens=tuple(sorted(tokenize(signature))),
        discriminative=discriminative,
        exception_types=exceptions,
        error_codes=codes,
        symbols=symbols_t,
        packages=packages,
        frames=frames,
        files=files,
        versions=versions,
    )
