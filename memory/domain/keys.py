"""Fact keys.

Two facts sharing a key cannot both be true. That single rule is what turns
"she moved" into a retirement rather than a second opinion — and it is why
getting the key wrong is the most expensive mistake in the whole system.

The rule cuts both ways, asymmetrically. A key too narrow costs a duplicate
row: visible, harmless. A key too broad costs a deletion, and that failure is
quiet — the fact is simply gone the next time you look.
"""

import re
from typing import Optional

from memory.constants import QUALIFIED_TOPICS

# Words too common to tell two facts apart.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "with",
        "for",
        "from",
        "that",
        "this",
        "their",
        "they",
        "his",
        "her",
        "its",
        "in",
        "on",
        "at",
        "of",
        "to",
        "by",
        "as",
        "it",
        "user",
        "he",
        "she",
        "will",
        "be",
        "been",
        "prefers",
        "likes",
        "uses",
    }
)


def slugify(value: Optional[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return slug.strip("-")


def qualifier_from_statement(statement: Optional[str]) -> str:
    """Build a qualifier out of the statement when the model did not supply one.

    Without this a qualified topic with a missing qualifier collapses onto the
    bare topic — ``note`` — and every unrelated note collides again. Two or
    three distinctive words is enough to keep separate things separate.
    """
    words = [
        word
        for word in slugify(statement).split("-")
        if len(word) > 2 and word not in _STOPWORDS
    ]
    return "-".join(words[:3])


def downgraded_key(profile_key: str, statement: Optional[str] = None) -> str:
    """The key for a profile line that was sent to the archive instead.

    It keeps its heading as a namespace and is qualified by its own words, so
    two preferences downgraded under the same heading do not retire one another.
    """
    slug = qualifier_from_statement(statement)
    return f"{profile_key}:{slug}" if slug else profile_key


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
    """The key for a procedure: its trigger, qualified by the rule.

    The trigger is the namespace and the rule is the qualifier — keyed on the
    trigger alone, "never use emoji and keep the subject under 50 characters"
    had the second rule retire the first on the way in. Namespaced under
    ``when:`` so a procedure can never collide with a fact.
    """
    # The namespace already says "when", and the model reliably says it too —
    # without the strip, "when reviewing my code" reads back as
    # "When when reviewing my code".
    situation = re.sub(
        r"^(when|while|whenever|during|for|if)-", "", slugify(trigger)
    ) or qualifier_from_statement(rule)
    if not situation:
        return "when:general"

    aspect = qualifier_from_statement(rule)
    return f"when:{situation}:{aspect}" if aspect else f"when:{situation}"
