from rest_framework import serializers

from assistant.constants import MESSAGE_MAX_LENGTH, PAGE_PATH_MAX_LENGTH
from assistant.models import AssistantMessage, FileOrganizationProposal


class FileOrganizationProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileOrganizationProposal
        fields = ("id", "status", "summary", "plan", "outcome")
        read_only_fields = fields


class AssistantMessageSerializer(serializers.ModelSerializer):
    proposals = FileOrganizationProposalSerializer(many=True, read_only=True)

    class Meta:
        model = AssistantMessage
        fields = (
            "id",
            "role",
            "content",
            "status",
            "tool_calls",
            "proposals",
            "created_at",
        )
        read_only_fields = fields


class AssistantUsageSerializer(serializers.Serializer):
    used_today = serializers.IntegerField()
    daily_limit = serializers.IntegerField()


class AssistantThreadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    messages = AssistantMessageSerializer(many=True)
    usage = AssistantUsageSerializer()


class AssistantSendSerializer(serializers.Serializer):
    """Socket ``send_message`` payload."""

    message = serializers.CharField(max_length=MESSAGE_MAX_LENGTH, trim_whitespace=True)
    path = serializers.RegexField(
        r"^/[\w\-/]*$", max_length=PAGE_PATH_MAX_LENGTH, default="/"
    )
