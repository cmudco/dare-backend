"""The layered memory store.

Three tables, mirroring the reference prototype's schema:

- ``MemoryRecord`` — facts and rules, one archive. Qualified keys, supersession
  chains, two timelines, and the embedding live on the row.
- ``MemoryLedgerEntry`` — every write decision, including refusals. A memory
  system you cannot audit is just a text file that grows.
- ``UserMemoryDocument`` — USER.md, one short markdown document per user,
  always injected, never searched.

The transcript layer deliberately has no table here: it is the existing
``conversations.Message`` rows, searched word-for-word by the
``search_sessions`` tool.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from pgvector.django import VectorField

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from memory.constants import (
    EMBED_DIMS,
    MemoryKind,
    MemoryState,
    Sensitivity,
    WriterAction,
)


class MemoryRecord(BaseModel):
    """One fact or rule in the archive.

    ``state`` and soft-deletion are orthogonal on purpose: ``state`` is the
    *system's* lifecycle (retired by a newer fact, gated for privacy) while
    ``is_deleted`` is the *user's* deletion (asked to forget it). Retrieval
    requires both. Use :meth:`usable` rather than filtering on the bare string
    "active" — the overlap with ``active_objects``' ``is_active`` naming is a
    standing hazard, so the state filter should never appear bare at a call
    site.
    """

    # UUID rather than the project's BigAutoField default: the prototype's ids
    # are strings, ledger rows reference them, and the FE types `id: string`.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_records",
        help_text=_("The person this memory is about. Never cross-user."),
    )
    kind = models.CharField(
        max_length=16,
        choices=MemoryKind.choices,
        help_text=_("A fact about the person, or a rule for a situation."),
    )
    key = models.CharField(
        max_length=255,
        default="",
        blank=True,
        help_text=_(
            "Qualified topic key. Two rows of one kind sharing a key cannot "
            "both be true — that collision is what turns 'I moved' into a "
            "retirement instead of a contradiction."
        ),
    )
    text = models.TextField(help_text=_("The statement, third person, standalone."))
    state = models.CharField(
        max_length=16,
        choices=MemoryState.choices,
        default=MemoryState.ACTIVE,
        help_text=_("active / superseded / held. Retire, never delete."),
    )
    sensitivity = models.CharField(
        max_length=16,
        choices=Sensitivity.choices,
        default=Sensitivity.NONE,
        help_text=_("health is gated (held); safety is never gated."),
    )
    source_conversation = models.ForeignKey(
        "conversations.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memory_records",
        help_text=_(
            "Where this was learned. Losing the conversation keeps the memory."
        ),
    )
    source_message = models.ForeignKey(
        "conversations.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memory_records",
        help_text=_("The user message this was extracted from."),
    )
    # Two timelines: created_at (from TimeStampMixin) is when we FOUND OUT;
    # valid_from/valid_until are when it was true in the world. They come
    # apart the moment someone says "I moved last month".
    occurred_at = models.DateField(null=True, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(
        null=True,
        blank=True,
        help_text=_(
            "A stated end date, or the day a replacement arrived. Null on "
            "slot topics (location, occupation...) — those get replaced, "
            "never expire."
        ),
    )
    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    replaces = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    importance = models.FloatField(default=0.5)
    confidence = models.FloatField(default=0.9)
    provenance = models.TextField(
        default="",
        blank=True,
        help_text=_("The sentence this came from, so the work can be checked."),
    )
    reinforced = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "How many times the person restated it verbatim — the only "
            "durability signal there is; consolidation promotes on it."
        ),
    )
    applies_when = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text=_(
            "For a rule: the situations it fires in, written out. This is what "
            "the rule is embedded as, because a trigger key plus a four-word "
            "imperative has almost no semantic surface — measured, "
            "'when:reviewing-code Be blunt' scored 0.17 against 'here is my "
            "function, take a look' and lost to an unrelated SQL rule at 0.28. "
            "Described properly the same rule scores 0.35 and wins."
        ),
    )
    pinned_to = models.CharField(
        max_length=40,
        blank=True,
        default="",
        help_text=_(
            "USER.md heading this fact is shown under, or empty. USER.md is a "
            "VIEW of the facts pinned into it, never a second copy: the row "
            "keeps the key, the dates and the supersession, and the document "
            "renders from whatever is pinned. So a pinned fact still retires "
            "itself when a newer one arrives, and the profile follows without "
            "anyone editing markdown."
        ),
    )
    # Storage plus stage-2 scoring only: stage 1 narrows by SQL indexes, then
    # ~50 vectors are scored in Python. No ANN index needed at per-user scale;
    # adding one later is a single additive CREATE INDEX.
    embedding = VectorField(dimensions=EMBED_DIMS, null=True, blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        indexes = [
            # The collision seek: "does anything already claim this key?" must
            # be one indexed lookup, not a scan.
            models.Index(
                fields=["user", "kind", "key", "state"], name="memrec_collision_idx"
            ),
            models.Index(
                fields=["user", "state", "-importance"], name="memrec_importance_idx"
            ),
            models.Index(
                fields=["user", "state", "-created_at"], name="memrec_recent_idx"
            ),
        ]

    def __str__(self):
        return f"[{self.kind}:{self.state}] {self.key} · {self.text[:60]}"

    @classmethod
    def usable(cls, user):
        """Rows retrieval may see: not user-deleted, and live in the state
        machine. Held and superseded rows are excluded here and added back
        explicitly by the callers that mean it."""
        return cls.active_objects.filter(user=user, state=MemoryState.ACTIVE)

    @classmethod
    def visible(cls, user):
        """Rows the user may see in the UI: everything they have not deleted."""
        return cls.active_objects.filter(user=user)


class MemoryLedgerEntry(BaseModel):
    """One write-path decision, applied or refused.

    Append-only. ``source_message`` doubles as the writer job's idempotency
    key: a ledger row for a message means that turn was fully ingested.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_ledger_entries",
    )
    action = models.CharField(
        max_length=32,
        choices=WriterAction.choices,
        help_text=_("What the application actually did."),
    )
    proposed_action = models.CharField(
        max_length=32,
        choices=WriterAction.choices,
        help_text=_("What the model asked for. Divergence is the audit trail."),
    )
    reason = models.TextField(default="", blank=True)
    note = models.TextField(
        null=True,
        blank=True,
        help_text=_("The rule that fired, in words a person can read."),
    )
    applied = models.BooleanField(default=False)
    record = models.ForeignKey(
        MemoryRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ledger_entries",
    )
    detail = models.TextField(default="", blank=True)
    source_text = models.TextField(
        default="",
        blank=True,
        help_text=_("The user message that caused this decision."),
    )
    source_message = models.ForeignKey(
        "conversations.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memory_ledger_entries",
    )
    proposal = models.JSONField(
        null=True,
        blank=True,
        help_text=_("The model's raw decision, pre-rules, for checking the work."),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"], name="memledger_scope_idx"),
        ]
        verbose_name_plural = "memory ledger entries"

    def __str__(self):
        flag = "applied" if self.applied else "refused"
        return f"{self.action} ({flag}) · {self.detail[:60]}"


class UserMemoryDocument(BaseModel):
    """USER.md — the always-injected profile document, one per user.

    A markdown TextField rather than a file store (the ``ResearcherProfile``
    precedent): being hand-editable through the API is the point, and the
    normalizer in memory.domain.user_doc is the single gate both machine and
    human writes pass through. The token budget is always derived, never
    stored.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_document",
    )
    content = models.TextField(default="", blank=True)

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    def __str__(self):
        return f"USER.md for user {self.user_id}"
