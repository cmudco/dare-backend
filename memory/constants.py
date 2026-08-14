"""Shared vocabulary and measured thresholds for memory."""

import re

from django.db import models
from django.utils.translation import gettext_lazy as _

# Records and ledger


class MemoryKind(models.TextChoices):
    FACT = "fact", _("Fact")
    PROCEDURE = "procedure", _("Procedure")


class MemoryState(models.TextChoices):
    ACTIVE = "active", _("Active")
    SUPERSEDED = "superseded", _("Superseded")
    HELD = "held", _("Held")


class Sensitivity(models.TextChoices):
    NONE = "none", _("None")
    HEALTH = "health", _("Health")
    SAFETY = "safety", _("Safety")
    THIRD_PARTY = "third-party", _("Third party")


class WriterAction(models.TextChoices):
    # PATCH_USER remains for historical ledger rows.
    PATCH_USER = "patch_user", _("Patch USER.md")
    ADD_FACT = "add_fact", _("Add fact")
    ADD_PROCEDURE = "add_procedure", _("Add procedure")
    SUPERSEDE = "supersede", _("Supersede")
    IGNORE = "ignore", _("Ignore")
    SEARCH_SESSIONS = "search_sessions", _("Search sessions")
    CONSOLIDATE = "consolidate", _("Consolidate")
    FORGET = "forget", _("Forget")
    EDIT = "edit", _("Edit")
    HOLD = "hold", _("Hold")
    RELEASE = "release", _("Release")
    IMPORT = "import", _("Import")


# Keys

TOPICS = (
    "name",
    "style",
    "diet",
    "diet_avoid",
    "schedule",
    "location",
    "occupation",
    "industry",
    "habit",
    "health",
    "person",
    "project",
    "boundaries",
    "note",
)

# These topics need qualifiers because multiple values may coexist.
QUALIFIED_TOPICS = frozenset(
    {
        "person",
        "health",
        "habit",
        "project",
        "schedule",
        "diet_avoid",
        "boundaries",
        "note",
        "style",
    }
)
NEVER_EXPIRES = frozenset({"location", "occupation", "industry", "name"})
PINNED_TOPIC_HEADINGS = {
    "name": "identity",
    "location": "identity",
    "boundary": "boundaries",
    "boundaries": "boundaries",
}


# USER.md

TOKEN_BUDGET = 500
TOKEN_WARNING = round(TOKEN_BUDGET * 0.8)


# Consolidation

MERGE_SIMILARITY = 0.74
MERGE_DISJOINT_SIMILARITY = 0.85
SNAP_SIMILARITY = 0.80
PROMOTE_AFTER_TELLINGS = 2
MAX_PROPOSALS = 12
MAX_PER_KIND = 4


# Retrieval

RANK_WEIGHTS = {
    "semantic": 0.5,
    "lexical": 0.2,
    "importance": 0.15,
    "recency": 0.1,
    "confidence": 0.05,
}

SCORE_FLOOR = 0.3
RELEVANCE_FLOOR = 0.40
SAFETY_RELEVANCE_FLOOR = 0.12
LEXICAL_RELEVANCE_MIN = 0.05
TOP_K = 3
RECENCY_HALF_LIFE_DAYS = 90

PROCEDURE_FLOOR = 0.22
PROCEDURE_TOP_K = 5
PROCEDURE_SHORTLIST_LIMIT = 24
PROCEDURE_RELEVANCE_FLOOR = 0.22

SHORTLIST_LIMIT = 50
SHORTLIST_LEXICAL_SHARE = 0.6
SHORTLIST_IMPORTANCE_SHARE = 0.25
SHORTLIST_RECENT_SHARE = 0.25
SHORTLIST_SEMANTIC_SHARE = 0.5

KEY_SPACE_LIMIT = 300
WRITER_RETRIEVE_TOP_K = 12
WRITER_RETRIEVE_FLOOR = 0.2
WRITER_RETRIEVE_SHORTLIST_LIMIT = 60

HISTORICAL_RE = re.compile(
    r"\b(used to|previously|before|back then|originally|no longer|last year"
    r"|in the past|history|ever lived|anywhere else|anyone else|anything else"
    r"|ever been|did i)\b",
    re.IGNORECASE,
)


# Embeddings

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 512
