"""Split an upstream body into retrievable content units.

Troubleshooting text is not uniform prose: a discussion body is usually a
sentence of context plus a pasted stack trace plus a config snippet. Those want
different retrieval treatment (exact/lexical on logs, dense on prose), so we
separate them at ingest instead of chunking blindly by token count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([\w.+-]*)[ \t]*$")

# Signals that a fenced block is machine output rather than source code.
_LOG_SIGNALS = (
    re.compile(r"\b(?:Error|Exception|Traceback|panic|FATAL|WARN|ERR!)\b"),
    re.compile(r"^\s+at\s+\S+", re.MULTILINE),
    re.compile(r"^\s*File \".*\", line \d+", re.MULTILINE),
    re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception)\b"),
    re.compile(r"\b(?:ENOENT|ECONNREFUSED|ETIMEDOUT|EACCES|MODULE_NOT_FOUND)\b"),
)

_CONFIG_LANGS = {"json", "jsonc", "json5", "yaml", "yml", "toml", "ini", "env", "dotenv"}
_MAX_PROSE_CHARS = 1500


@dataclass(frozen=True)
class ContentUnitDraft:
    unit_type: str  # prose | code | log | config
    seq: int
    text: str
    lang: str | None = None


def _looks_like_log(text: str) -> bool:
    return any(rx.search(text) for rx in _LOG_SIGNALS)


def _classify_block(lang: str, text: str) -> str:
    lowered = lang.lower()
    if lowered in _CONFIG_LANGS:
        return "config"
    if lowered in {"log", "logs", "console", "output", "text", "txt", ""} and _looks_like_log(text):
        return "log"
    if _looks_like_log(text) and lowered in {"bash", "sh", "shell", "powershell", "ps1"}:
        return "log"
    return "code" if lowered else ("log" if _looks_like_log(text) else "code")


def _split_prose(text: str) -> list[str]:
    """Paragraph-ish splitting with a hard cap, so one unit stays one idea."""
    parts: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > _MAX_PROSE_CHARS:
            cut = para.rfind("\n", 0, _MAX_PROSE_CHARS)
            if cut <= 0:
                cut = _MAX_PROSE_CHARS
            parts.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            parts.append(para)
    return parts


def split_body(body: str | None) -> list[ContentUnitDraft]:
    """Return ordered content units for one body. Fenced blocks are preserved whole."""
    if not body or not body.strip():
        return []

    units: list[ContentUnitDraft] = []
    seq = 0
    buffer: list[str] = []
    fence: str | None = None
    fence_lang = ""
    block: list[str] = []

    def flush_prose() -> None:
        nonlocal seq, buffer
        if not buffer:
            return
        for chunk in _split_prose("\n".join(buffer)):
            units.append(ContentUnitDraft(unit_type="prose", seq=seq, text=chunk))
            seq += 1
        buffer = []

    for line in body.splitlines():
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                flush_prose()
                fence = match.group(1)
                fence_lang = match.group(2) or ""
                block = []
            else:
                buffer.append(line)
            continue

        # Inside a fence: only a same-style fence of >= length closes it.
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
            text = "\n".join(block).strip("\n")
            if text.strip():
                units.append(
                    ContentUnitDraft(
                        unit_type=_classify_block(fence_lang, text),
                        seq=seq,
                        text=text,
                        lang=fence_lang or None,
                    )
                )
                seq += 1
            fence = None
            fence_lang = ""
            block = []
        else:
            block.append(line)

    if fence is not None and block:  # unterminated fence: keep the content anyway
        text = "\n".join(block).strip("\n")
        if text.strip():
            units.append(
                ContentUnitDraft(
                    unit_type=_classify_block(fence_lang, text),
                    seq=seq,
                    text=text,
                    lang=fence_lang or None,
                )
            )
            seq += 1
    flush_prose()
    return units
