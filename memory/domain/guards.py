"""Deterministic write guards shared by the reply and memory gate."""

import math
import re
from collections import Counter

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_SPACED_RUN_RE = re.compile(r"\b(?:[A-Za-z0-9][ .\-_]){3,}[A-Za-z0-9]\b")
_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]{20,}")

_SECRET_SHAPE_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AIza[0-9A-Za-z_\-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,})"
)
_CREDENTIAL_ASSIGN_RE = re.compile(
    r"\b(password|passcode|passphrase|api[ _-]?key|access[ _-]?key"
    r"|secret|auth[ _-]?token|access[ _-]?token|admin[ _-]?token"
    r"|token|private[ _-]?key|credentials?)\b"
    r"[^.\n]{0,40}?"
    r"(is|was|are|[:=])\s*"
    r"['\"]?[A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*['\"]?",
    re.IGNORECASE,
)
_OVERRIDE_RE = re.compile(
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?"
    r"|guidelines|rules|system prompt|previous (instructions?|prompts?))\b"
    r"|\byou (are|will) (now|no longer)\b"
    r"|\bpretend (you are|to be)\b"
    r"|\bnew (system )?(prompt|instructions?)\b",
    re.IGNORECASE,
)
_AUTHORITY_DEMAND_RE = re.compile(
    r"\b(disable|deactivate|bypass|turn off|remove|drop)\b[^.\n]{0,30}"
    r"\b(your|the|all|any)\b[^.\n]{0,30}"
    r"\b(filters?|safeguards?|safety|restrictions?|guardrails?|guidelines)\b",
    re.IGNORECASE,
)


def deobfuscate(text: str) -> str:
    """Remove spacing tricks from a copy used only for detection."""
    plain = _ZERO_WIDTH_RE.sub("", text)
    return _SPACED_RUN_RE.sub(
        lambda match: re.sub(r"[ .\-_]", "", match.group()), plain
    )


def _bits_per_char(token: str) -> float:
    counts = Counter(token)
    total = len(token)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _reads_like_key_material(text: str) -> bool:
    """Detect long, mixed, high-entropy tokens with interleaved digits."""
    for raw in _ENTROPY_TOKEN_RE.findall(text):
        token = re.sub(r"[\-_/+=]", "", raw)
        if len(token) < 20 or token.isalpha() or token.isdigit():
            continue
        transitions = sum(
            1
            for left, right in zip(token, token[1:])
            if left.isalpha() != right.isalpha()
        )
        if transitions >= 4 and _bits_per_char(token) >= 3.0:
            return True
    return False


def looks_like_secret(text: str) -> bool:
    for candidate in (text, deobfuscate(text)):
        if (
            _SECRET_SHAPE_RE.search(candidate)
            or _CREDENTIAL_ASSIGN_RE.search(candidate)
            or _reads_like_key_material(candidate)
        ):
            return True
    return False


def demands_override(text: str) -> bool:
    """Return whether the text asks for the assistant's rules to stop applying."""
    for candidate in (text, deobfuscate(text)):
        if _OVERRIDE_RE.search(candidate) or _AUTHORITY_DEMAND_RE.search(candidate):
            return True
    return False
