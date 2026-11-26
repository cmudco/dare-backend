"""Serializers for the memory API.

Wire casing is handled globally (djangorestframework-camel-case), so these
declare snake_case and the frontend sees ``memoryType`` / ``createdAt``.
"""

from rest_framework import serializers

from memory.constants import OmpOutcome
from memory.models import MemoryBackfillRun
from memory.services import omp_import


class MemoryItemSerializer(serializers.Serializer):
    """One compat item — a USER.md line, a fact, or a rule, flattened."""

    id = serializers.CharField(read_only=True)
    memory_type = serializers.CharField(read_only=True)
    content = serializers.CharField(read_only=True)
    categories = serializers.ListField(child=serializers.CharField(), read_only=True)
    created_at = serializers.CharField(read_only=True, required=False, allow_null=True)
    updated_at = serializers.CharField(read_only=True, required=False, allow_null=True)
    score = serializers.FloatField(read_only=True, required=False)
    state = serializers.CharField(read_only=True, required=False, allow_null=True)
    valid_until = serializers.CharField(read_only=True, required=False, allow_null=True)
    replaced_by = serializers.CharField(read_only=True, required=False, allow_null=True)


class OmpExportQuerySerializer(serializers.Serializer):
    include_sensitive = serializers.BooleanField(
        default=False,
        help_text="Also export held memories and ones labelled health or third-party.",
    )


class OmpReceiptEntrySerializer(serializers.Serializer):
    kind = serializers.CharField(help_text="memory or tombstone")
    origin = serializers.CharField()
    identifier = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=OmpOutcome.choices)
    reason = serializers.CharField(allow_blank=True)
    record_id = serializers.CharField(allow_blank=True)


class MemoryImportRunSerializer(serializers.Serializer):
    """An import and its receipt: what happened to each memory in the file."""

    id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    origin = serializers.CharField()
    counts = serializers.SerializerMethodField()
    held = serializers.SerializerMethodField(
        help_text="Imported memories, from any import, still waiting to be released."
    )
    entries = OmpReceiptEntrySerializer(many=True)

    def get_counts(self, run) -> dict:
        counts = {status: 0 for status in OmpOutcome.values}
        for entry in run.entries:
            if entry["kind"] == "memory":
                counts[entry["status"]] += 1
        return counts

    def get_held(self, run) -> int:
        return omp_import.held_imports_count(run.user)


class OmpReleaseResponseSerializer(serializers.Serializer):
    released = serializers.IntegerField()
    still_held = serializers.IntegerField()


class MemorySearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(
        required=True,
        min_length=1,
        max_length=1000,
        help_text="The search query to find relevant memories",
    )


class MemorySearchResponseSerializer(serializers.Serializer):
    query = serializers.CharField(read_only=True)
    items = MemoryItemSerializer(many=True, read_only=True)
    categories = serializers.ListField(
        child=serializers.DictField(),
        read_only=True,
        help_text="Cluster-level summaries shown as 'matched clusters' chips",
    )


class ClearResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(read_only=True)
    message = serializers.CharField(read_only=True)


class MemoryBackfillRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemoryBackfillRun
        fields = (
            "id",
            "status",
            "total_turns",
            "processed_turns",
            "since",
            "until",
            "created_at",
            "started_at",
            "completed_at",
            "error_message",
        )
        read_only_fields = fields


class MemoryBackfillResponseSerializer(serializers.Serializer):
    run = MemoryBackfillRunSerializer(read_only=True, allow_null=True)


class MemoryBackfillRequestSerializer(serializers.Serializer):
    since = serializers.DateField(required=False, allow_null=True)
    until = serializers.DateField(required=False, allow_null=True)

    def validate(self, attrs):
        since = attrs.get("since")
        until = attrs.get("until")
        if since is not None and until is not None and since > until:
            raise serializers.ValidationError(
                {"until": "End date must be on or after the start date."}
            )
        return attrs
