"""Constants for the layered memory system.

The numbers here are not tunables to revisit casually: every floor, weight and
threshold was set by a measured failure in the reference prototype
(memory-explore/memory-app) and is asserted by the test suite. Change them with
an evaluation run, not an opinion.
"""

import re

from django.db import models
from django.utils.translation import gettext_lazy as _


class MemoryKind(models.TextChoices):
    """What a memory row is: a fact about the person, or a rule for a situation."""

    FACT = "fact", _("Fact")
    PROCEDURE = "procedure", _("Procedure")


class MemoryState(models.TextChoices):
    """The system's lifecycle for a row.

    Orthogonal to BaseModel soft-deletion: ``state`` is what the *system* did
    (retired by a newer fact, gated for privacy); ``is_deleted`` is what the
    *user* did (asked to forget it). Retrieval requires both ``is_deleted=False``
    and ``state=ACTIVE``.
    """

    ACTIVE = "active", _("Active")
    SUPERSEDED = "superseded", _("Superseded")
    # Written down and visible to the user, but never retrieved into an answer.
    # Medical facts mentioned in passing land here; releasing one is a hand act.
    HELD = "held", _("Held")


class Sensitivity(models.TextChoices):
    NONE = "none", _("None")
    # Gate for privacy, where staying silent is free...
    HEALTH = "health", _("Health")
    # ...never for safety, where silence is the hazard. An allergy in an
    # approval queue is a system that books the seafood restaurant.
    SAFETY = "safety", _("Safety")
    THIRD_PARTY = "third-party", _("Third party")


class WriterAction(models.TextChoices):
    """Everything that can appear in the ledger's action column.

    The writer model may only *emit* the first five; ``search_sessions`` and
    ``consolidate`` are logged by the application when those events happen, so
    reads and sweeps share one auditable timeline with writes.
    """

    PATCH_USER = "patch_user", _("Patch USER.md")
    ADD_FACT = "add_fact", _("Add fact")
    ADD_PROCEDURE = "add_procedure", _("Add procedure")
    SUPERSEDE = "supersede", _("Supersede")
    IGNORE = "ignore", _("Ignore")
    SEARCH_SESSIONS = "search_sessions", _("Search sessions")
    CONSOLIDATE = "consolidate", _("Consolidate")
    # Not in the prototype's vocabulary: DARE's UI has a per-item "Forget"
    # and an inline edit, and pretending a user's own action is an "ignore"
    # or an "add_fact" would falsify the ledger.
    FORGET = "forget", _("Forget")
    EDIT = "edit", _("Edit")
    # Likewise honest names for the privacy gate's two hand-operated moves
    # (the prototype logged these as ignore/add_fact, which read as lies).
    HOLD = "hold", _("Hold")
    RELEASE = "release", _("Release")


# --- Keys -------------------------------------------------------------------

# What a fact can be about. An enum, not free text: left open, a model files a
# report deadline under "schedule" and a restaurant's neighbourhood under
# "location", and both retire a fact that never changed.
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
    "note",
)

# Topics where two things can be true at once, so the key carries a qualifier.
# Every entry was found by a real collision (a certificate retiring a bank
# account under bare `note`; answer length retiring answer format under `style`).
QUALIFIED_TOPICS = frozenset(
    {"person", "health", "habit", "project", "schedule", "diet_avoid", "note", "style"}
)

# Topics where a person always has exactly one answer, so the fact never
# expires — it only ever gets replaced. An expiry on a slot like this leaves
# the store with NO answer at all, which is strictly worse than a stale one.
NEVER_EXPIRES = frozenset({"location", "occupation", "industry", "name"})

# USER.md headings that hold instructions rather than disclosures — how to
# address someone and how to answer them. These reach the profile without an
# explicit request, because their whole value is applying to the next turn
# and every turn after, and a preference that only surfaces when a question
# happens to sound like it is a preference the person has to keep repeating.
# Everything else about a person's life still needs to be asked for.
ADDRESSING_HEADINGS = frozenset({"communication", "identity"})

# --- USER.md ----------------------------------------------------------------

# Roughly 500 tokens. The whole file is injected on every single turn.
TOKEN_BUDGET = 500
TOKEN_WARNING = round(TOKEN_BUDGET * 0.8)

# --- Retrieval --------------------------------------------------------------

# How much each signal counts. Meaning dominates because it is the only signal
# that survives paraphrase; lexical is the only one that nails exact tokens.
RANK_WEIGHTS = {
    "semantic": 0.5,
    "lexical": 0.2,
    "importance": 0.15,
    "recency": 0.1,
    "confidence": 0.05,
}

# Below this, we inject nothing. Returning nothing is a correct answer that a
# top-k with no floor can never give.
SCORE_FLOOR = 0.3

# The row must actually be ABOUT something related: relevance qualifies,
# importance only ranks. A row can be important, recent and certain and still
# have nothing to do with what was asked.
#
# Benched against a labelled query set on a real store (18 queries, 22 facts):
# true matches score 0.26-0.67 on meaning, unrelated rows 0.02-0.24. At 0.12
# precision was 0.36 and every turn carried 1.7 irrelevant memories — a code
# review pulled in a bouldering habit. 0.28 is where F1 peaks (0.73) and noise
# falls to 0.17 per turn.
RELEVANCE_FLOOR = 0.28

# Safety rows keep the old, looser bar: nothing was relaxed for them, the rest
# was tightened around them. The asymmetry is the point — failing to recall a
# bouldering habit costs nothing, failing to recall a peanut allergy on "book
# me a restaurant" is the hazard the archive exists to prevent, and that pair
# benches at 0.16 while the ordinary floor now sits at 0.28.
#
# Measured against the stored allergy vector: turns that risk food score
# 0.13-0.18 (restaurant .162, dinner tonight .132, cafe .180), turns that do
# not score 0.11 and below. A narrow margin, so it is deliberately set at the
# bottom of the true range rather than the middle — on this gate a false
# positive is a wasted line and a false negative is the whole failure.
SAFETY_RELEVANCE_FLOOR = 0.12

# Lexical rank is normalised against the best candidate in the batch so it can
# be weighed against the other signals — which means the best row in a batch
# always reads as a perfect 1.0, however bad it is in absolute terms. That is
# fine for ranking and wrong for qualifying: "explain how TCP handshakes work"
# matched an unrelated fact on the single stem "work" at ts_rank 0.015, was
# normalised to 1.0, and sailed through the gate. Real matches measured 0.06
# and up, junk an order of magnitude below, so qualification reads the raw
# score and only ranking sees the normalised one.
LEXICAL_RELEVANCE_MIN = 0.05

TOP_K = 3
RECENCY_HALF_LIFE_DAYS = 90

# Procedures are few and cheap to include, so cast wider than for facts. One
# extra rule is a line the model can ignore; one missing rule is repeating a
# mistake the person already corrected.
PROCEDURE_FLOOR = 0.22
PROCEDURE_TOP_K = 5
PROCEDURE_SHORTLIST_LIMIT = 24

# Same reasoning one notch looser: a rule that misses costs a repeated mistake,
# so procedures keep casting wider than facts here too. It still has to bite —
# at the old 0.12 a request to review a Python function pulled in the rule
# about formatting paper summaries.
PROCEDURE_RELEVANCE_FLOOR = 0.22

# Stage-one shortlist cap and its split between the three unioned sources.
SHORTLIST_LIMIT = 50
SHORTLIST_LEXICAL_SHARE = 0.6
SHORTLIST_IMPORTANCE_SHARE = 0.25
SHORTLIST_RECENT_SHARE = 0.25

# How many existing keys the writer is shown. Keys are the collision domain —
# reusing one is what lets a new fact retire an old one — so the writer has to
# see the slots that exist, not just the rows retrieval happened to surface.
# A key costs about four tokens, so this cap is generous on purpose.
KEY_SPACE_LIMIT = 300

# The writer's own retrieval casts wider than the read path: recall thrown away
# before the collision check can never come back.
WRITER_RETRIEVE_TOP_K = 12
WRITER_RETRIEVE_FLOOR = 0.2
WRITER_RETRIEVE_SHORTLIST_LIMIT = 60

# --- Regexes ----------------------------------------------------------------

# "Remember that..." is consent in the person's own words — the only thing that
# lets a plain preference reach USER.md directly.
# Historical phrasing widens retrieval to superseded rows — and only to
# superseded rows. Held rows are never candidates, whatever the phrasing.
HISTORICAL_RE = re.compile(
    r"\b(used to|previously|before|back then|originally|no longer|last year"
    r"|in the past|history|ever lived|anywhere else|anyone else|anything else"
    r"|ever been|did i)\b",
    re.IGNORECASE,
)

# --- Embeddings -------------------------------------------------------------

EMBED_MODEL = "text-embedding-3-small"
# Truncated from 1536: ~97% retrieval quality at a third of the storage.
EMBED_DIMS = 512
