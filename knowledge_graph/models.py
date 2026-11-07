from django.conf import settings
from django.db import models

from common.models import TimeStampMixin
from files.models import DocumentChunk, File
from knowledge_graph.constants import AliasSource, GraphStatus, LabelSource


class EntityCluster(TimeStampMixin):
    """One real-world thing, known under one or more entity keys.

    Membership lives at key level so it survives the delete-and-recreate of
    ``DocumentEntity`` rows on every reprocess.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="entity_clusters",
    )
    label = models.CharField(max_length=200)
    kind = models.CharField(max_length=32)

    class Meta:
        ordering = ["label"]
        indexes = [models.Index(fields=["user", "label"])]

    def __str__(self):
        return self.label


class ClusterAlias(TimeStampMixin):
    """A normalized entity key and the cluster that owns it for this user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    cluster = models.ForeignKey(
        EntityCluster, on_delete=models.CASCADE, related_name="aliases"
    )
    key = models.CharField(max_length=200)
    text = models.CharField(max_length=200, help_text="Spelling shown to the user.")
    source = models.CharField(
        max_length=16, choices=AliasSource.choices, default=AliasSource.SELF
    )
    probability = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key"], name="unique_cluster_alias_per_user"
            )
        ]


class AliasDecision(TimeStampMixin):
    """A remembered same/different verdict, so a pair is never re-asked or re-merged."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    key_a = models.CharField(max_length=200)
    key_b = models.CharField(max_length=200)
    same = models.BooleanField()
    source = models.CharField(max_length=16, choices=AliasSource.choices)
    probability = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key_a", "key_b"], name="unique_alias_decision"
            ),
            models.CheckConstraint(
                # Not key_a < key_b: Python orders the pair, and database collations
                # sort spaces differently.
                condition=~models.Q(key_a=models.F("key_b")),
                name="alias_decision_keys_differ",
            ),
        ]


class KnowledgeGraph(TimeStampMixin):
    """A named set of files whose relationships the user chose to pay for."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="knowledge_graphs",
    )
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True, default="")
    goal = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="What the user wants to learn; steers suggested relationship types.",
    )
    files = models.ManyToManyField(File, related_name="knowledge_graphs")
    status = models.CharField(
        max_length=24, choices=GraphStatus.choices, default=GraphStatus.READY_FREE
    )
    error = models.CharField(max_length=300, blank=True, default="")
    job_id = models.CharField(max_length=64, blank=True, default="")
    chunks_total = models.PositiveIntegerField(default=0)
    chunks_done = models.PositiveIntegerField(default=0)
    tokens_used = models.PositiveIntegerField(default=0)
    approved_at = models.DateTimeField(null=True, blank=True)
    built_at = models.DateTimeField(null=True, blank=True)
    eval_report = models.JSONField(null=True, blank=True)
    proposed_types = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class RelationLabel(TimeStampMixin):
    """One relationship type the graph may use; the list is capped and editable."""

    graph = models.ForeignKey(
        KnowledgeGraph, on_delete=models.CASCADE, related_name="labels"
    )
    name = models.CharField(max_length=48)
    description = models.CharField(max_length=200, blank=True, default="")
    source = models.CharField(
        max_length=16, choices=LabelSource.choices, default=LabelSource.DISCOVERED
    )
    read_at = models.DateTimeField(
        null=True, blank=True, help_text="When a build last read chunks for this type."
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["graph", "name"], name="unique_relation_label_per_graph"
            )
        ]

    def __str__(self):
        return self.name


class EntityRelation(TimeStampMixin):
    """A typed fact stated by one chunk: subject, relation, object."""

    graph = models.ForeignKey(
        KnowledgeGraph, on_delete=models.CASCADE, related_name="relations"
    )
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="+")
    chunk = models.ForeignKey(DocumentChunk, on_delete=models.CASCADE, related_name="+")
    subject = models.ForeignKey(
        EntityCluster, on_delete=models.CASCADE, related_name="subject_relations"
    )
    object = models.ForeignKey(
        EntityCluster, on_delete=models.CASCADE, related_name="object_relations"
    )
    label = models.ForeignKey(
        RelationLabel, on_delete=models.CASCADE, related_name="relations"
    )
    probability = models.FloatField()
    evidence = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-probability"]
        constraints = [
            models.UniqueConstraint(
                fields=["chunk", "subject", "object", "label"],
                name="unique_relation_per_chunk",
            )
        ]
