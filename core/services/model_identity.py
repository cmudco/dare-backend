"""Model family resolution for proxy-routed model identifiers.

A LiteLLM proxy advertises models by deployment address rather than by model
identity: ``bedrock/us.anthropic.claude-opus-4-5-20251101-v1:0`` carries a
route, a region, a vendor namespace and a version alongside the model itself.
Capability lookups keyed on DARE's own identifiers never matched those
strings, so proxy-routed models fell through to permissive defaults and sent
parameters their provider rejects.

This module owns that mapping and nothing else: a raw identifier goes in, a
``ModelFamily`` (or ``None``) comes out. It performs no I/O and touches no
ORM, so both the dispatch path and the model picker can call it.

Families are only declared where they change an outcome. Anything that
resolves to ``None`` keeps the permissive defaults, which is correct for the
ordinary chat models that accept every parameter DARE sends.
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# Ordered rewrites that reduce a deployment address to a model identity. Each
# applies at most once; order matters, since later patterns assume the
# prefixes are already gone. The vendor namespace becomes a hyphen rather
# than being deleted, because for some models it carries the only
# distinguishing token ("deepseek.r1" -> "deepseek-r1", not "r1").
_NORMALIZERS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"^[a-z0-9_]+/"), ""),
    (re.compile(r"^(?:us|eu|apac)\."), ""),
    (re.compile(r"^wine-"), ""),
    (
        re.compile(
            r"^(anthropic|openai|meta|google|deepseek|qwen|mistral|amazon|cohere)\."
        ),
        r"\1-",
    ),
    (re.compile(r"-v\d+(?::\d+)?$"), ""),
    (re.compile(r"-\d{8}$"), ""),
)

# Tier and channel variants of one model — gpt-5.6 sol/terra/luna — share a
# capability profile but are priced separately, so this suffix is removed for
# family matching and kept for price lookup.
_TIER_SUFFIX = re.compile(r"-(?:sol|terra|luna|preview)$")


_VENDOR_PREFIX = re.compile(
    r"^(?:anthropic|meta|google|deepseek|qwen|mistral|amazon|cohere|openai)-"
)


@dataclass(frozen=True)
class ModelFamily:
    """Capability profile shared by every deployment of one model family."""

    key: str
    patterns: Tuple[re.Pattern, ...]
    is_reasoning: bool = False
    supports_temperature: bool = True
    supports_effort: bool = False
    supports_adaptive_thinking: bool = False


FAMILIES: Tuple[ModelFamily, ...] = (
    ModelFamily(
        key="gpt-5-reasoning",
        patterns=(re.compile(r"(?:^|-)gpt-5(?:[.-]|$)"),),
        is_reasoning=True,
        supports_temperature=False,
    ),
    ModelFamily(
        key="claude-reasoning-effort",
        patterns=(re.compile(r"(?:^|-)claude-(?:opus-4-[78]|sonnet-5)(?:-|$)"),),
        supports_temperature=False,
        supports_effort=True,
        supports_adaptive_thinking=True,
    ),
    ModelFamily(
        key="deepseek-r1",
        patterns=(re.compile(r"(?:^|-)deepseek-r1(?:-|$)"),),
        is_reasoning=True,
        supports_temperature=False,
    ),
)


def _reduce(raw_identifier: str) -> str:
    """Strip deployment addressing, keeping the tier suffix."""
    value = (raw_identifier or "").strip().lower()
    for pattern, replacement in _NORMALIZERS:
        value = pattern.sub(replacement, value, count=1)
    return value


def normalize_identifier(raw_identifier: str) -> str:
    """Model identity for capability matching. Tier variants collapse here."""
    return _TIER_SUFFIX.sub("", _reduce(raw_identifier), count=1)


def pricing_keys(raw_identifier: str) -> Tuple[str, ...]:
    """Keys a raw identifier could match a separately-priced model under.

    The tier suffix is kept: gpt-5.6 sol, terra and luna differ by 25x in
    price, so collapsing them would bill one model at another's rate.

    A leading vendor namespace is kept too, since for some models it carries
    the only distinguishing token. DARE's own identifiers omit it, so matching
    the two sides needs the vendor-less spelling as well.
    """
    canonical = _reduce(raw_identifier)
    if not canonical:
        return ()
    stripped = _VENDOR_PREFIX.sub("", canonical, count=1)
    if stripped != canonical:
        return (canonical, stripped)
    return (canonical,)


def resolve_family(raw_identifier: str) -> Optional[ModelFamily]:
    """Return the family governing this model, or None to keep the defaults."""
    canonical = normalize_identifier(raw_identifier)
    if not canonical:
        return None
    for family in FAMILIES:
        if any(pattern.search(canonical) for pattern in family.patterns):
            return family
    return None
