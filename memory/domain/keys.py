"""Canonical keys for facts and procedures."""

import re
from typing import Optional

from memory.constants import QUALIFIED_TOPICS

_STOPWORDS = frozenset(
    "a an and are as at be been but by for from had has have he her his in "
    "is it its likes of on or prefers she that the their they this to user "
    "uses was were will with".split()
)


def slugify(value: Optional[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return slug.strip("-")


def qualifier_from_statement(statement: Optional[str]) -> str:
    """Derive a short qualifier when the writer omitted one."""
    words = [
        word
        for word in slugify(statement).split("-")
        if len(word) > 2 and word not in _STOPWORDS
    ]
    return "-".join(words[:3])


def downgraded_key(profile_key: str, statement: Optional[str] = None) -> str:
    """Key a refused profile line without colliding with its neighbours."""
    slug = qualifier_from_statement(statement)
    return f"{profile_key}:{slug}" if slug else profile_key


def distinguishing_key(key: str, statement: Optional[str]) -> str:
    """Extend an occupied additive key using fresh words from the statement."""
    key_words = set(slugify(key).split("-"))
    words = [
        word
        for word in slugify(statement).split("-")
        if len(word) > 2 and word not in _STOPWORDS and word not in key_words
    ]
    slug = "-".join(words[:3]) or "more"
    return f"{key}:{slug}"


def key_for(
    topic: str,
    qualifier: Optional[str] = None,
    statement: Optional[str] = None,
) -> str:
    """``health`` + ``peanut`` → ``health:peanut``. Unqualified topics keep the
    bare name; ``statement`` is the fallback qualifier source, so a qualified
    topic can never silently become an unqualified one."""
    if topic not in QUALIFIED_TOPICS:
        return topic

    slug = slugify(qualifier) or qualifier_from_statement(statement)
    return f"{topic}:{slug}" if slug else topic


def procedure_key(trigger: Optional[str], rule: Optional[str] = None) -> str:
    """Key a procedure by its trigger and rule."""
    situation = slugify(trigger)
    for prefix in ("when-", "while-", "whenever-", "during-", "for-", "if-"):
        if situation.startswith(prefix):
            situation = situation.removeprefix(prefix)
            break
    situation = situation or qualifier_from_statement(rule)
    if not situation:
        return "when:general"

    aspect = qualifier_from_statement(rule)
    return f"when:{situation}:{aspect}" if aspect else f"when:{situation}"
