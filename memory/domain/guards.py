"""Deterministic policy guards, shared by both sides of a turn.

The gate refuses what these detect, and the read path announces the same
verdict in the same turn — so what the assistant says and what the writer
later does come from one authority instead of two. Red-teamed: the assistant
told the user it would not store their credentials while the background
writer stored both, because the promise and the decision were made by
different components reading different rules.

Pure functions over text. The model gets no vote in any of them, which is
the point: the failures these close all began with a model agreeing.

Detection is layered rather than clever:

    known shapes    sk-…, AKIA…, ghp_…, JWTs, private keys
    labeled values  a credential noun possessing a concrete value
    entropy         an unlabeled dump that reads like key material
    defection       instructions to ignore, replace or bypass the rules

and every layer runs twice — once on the text as written and once
de-obfuscated, so "P a s s w o r d" and zero-width padding fail the same
way the plain form does. Instructions about how to BEHAVE ("answer in
Urdu", "keep it short") are procedures and welcome; what the defection
lexicon names is instructions to stop following the rules.
"""

import math
import re
from collections import Counter

from memory.constants import CREDENTIAL_ASSIGN_RE, OVERRIDE_RE, SECRET_SHAPE_RE

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")

# "C o d e x - P a s s - 7 7 2 1": four or more single characters kept apart
# by spaces, dots, dashes or underscores is a word being smuggled past a
# matcher, not prose.
_SPACED_RUN_RE = re.compile(r"\b(?:[A-Za-z0-9][ .\-_]){3,}[A-Za-z0-9]\b")

_ENTROPY_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]{20,}")

# "Disable your safety filters" carries no credential and no explicit
# "ignore your instructions", and still only exists to make the rules stop
# applying.
_AUTHORITY_DEMAND_RE = re.compile(
    r"\b(disable|deactivate|bypass|turn off|remove|drop)\b[^.\n]{0,30}"
    r"\b(your|the|all|any)\b[^.\n]{0,30}"
    r"\b(filters?|safeguards?|safety|restrictions?|guardrails?|guidelines)\b",
    re.IGNORECASE,
)


def deobfuscate(text: str) -> str:
    """The text with its disguises removed, for detection only.

    Never stored and never shown — collapsing "U S A" to "USA" would mangle
    real prose, which is fine in a copy that only a matcher reads.
    """
    plain = _ZERO_WIDTH_RE.sub("", text)
    return _SPACED_RUN_RE.sub(
        lambda match: re.sub(r"[ .\-_]", "", match.group()), plain
    )


def _bits_per_char(token: str) -> float:
    counts = Counter(token)
    total = len(token)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _reads_like_key_material(text: str) -> bool:
    """An unlabeled secret: long, mixed, high-entropy, digits interleaved.

    The interleaving requirement is what keeps ordinary long identifiers out —
    "convertUserProfileToJson2" and "my-very-long-page-slug-2026" put their
    digits at the end, key material scatters them throughout.
    """
    for raw in _ENTROPY_TOKEN_RE.findall(text):
        token = re.sub(r"[\-_/+=]", "", raw)
        if len(token) < 20 or token.isalpha() or token.isdigit():
            continue
        transitions = sum(
            1 for a, b in zip(token, token[1:]) if a.isalpha() != b.isalpha()
        )
        if transitions >= 4 and _bits_per_char(token) >= 3.0:
            return True
    return False


def looks_like_secret(text: str) -> bool:
    for candidate in (text, deobfuscate(text)):
        if (
            SECRET_SHAPE_RE.search(candidate)
            or CREDENTIAL_ASSIGN_RE.search(candidate)
            or _reads_like_key_material(candidate)
        ):
            return True
    return False


def demands_override(text: str) -> bool:
    """Whether the text asks for the assistant's rules to stop applying.

    Used on the TURN, where it refuses every write, and on a proposed row's
    TEXT, where it keeps the archive from carrying an instruction to defect —
    a poisoned procedure would otherwise be re-injected on every matching
    turn forever.
    """
    for candidate in (text, deobfuscate(text)):
        if OVERRIDE_RE.search(candidate) or _AUTHORITY_DEMAND_RE.search(candidate):
            return True
    return False
