from rest_framework import serializers

from assistant.constants import (
    MESSAGE_MAX_LENGTH,
    PAGE_PATH_MAX_LENGTH,
    PLAN_MAX_ACTIONS,
    ProposalActionStatus,
    ProposalActionType,
)
from assistant.models import AssistantMessage, AssistantProposal


class ProposalFileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class ProposalConversationSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()


class ProposalActionSerializer(serializers.Serializer):
    """One proposed change as the user sees it; the undo journal stays private."""

    id = serializers.CharField()
    type = serializers.ChoiceField(choices=ProposalActionType.choices)
    name = serializers.CharField(allow_blank=True)
    is_new = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
    files = ProposalFileSerializer(many=True)
    conversations = ProposalConversationSerializer(many=True)
    status = serializers.ChoiceField(choices=ProposalActionStatus.choices)
    notes = serializers.ListField(child=serializers.CharField())


class AssistantProposalSerializer(serializers.ModelSerializer):
    actions = ProposalActionSerializer(many=True, source="plan.actions")

    class Meta:
        model = AssistantProposal
        fields = ("id", "status", "summary", "actions")
        read_only_fields = fields


class ProposalActionIdsSerializer(serializers.Serializer):
    """Which actions to apply or undo; omitted means all of them."""

    action_ids = serializers.ListField(
        child=serializers.CharField(max_length=8),
        max_length=PLAN_MAX_ACTIONS,
        required=False,
    )


class AssistantMessageSerializer(serializers.ModelSerializer):
    proposals = AssistantProposalSerializer(many=True, read_only=True)

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
