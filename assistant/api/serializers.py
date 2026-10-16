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


class ProposalModelOptionSerializer(serializers.Serializer):
    model_ref = serializers.CharField()
    model_name = serializers.CharField()
    role = serializers.CharField(allow_null=True)
    output_usd_per_million_tokens = serializers.CharField(allow_null=True)


class ProposalAgentSerializer(serializers.Serializer):
    """The models a create_agent action offers; the user picks one on apply."""

    options = ProposalModelOptionSerializer(many=True)
    enable_web_search = serializers.BooleanField()
    selected_model_ref = serializers.CharField(allow_null=True)


class ProposalActionSerializer(serializers.Serializer):
    """One proposed change as the user sees it; the undo journal stays private."""

    id = serializers.CharField()
    type = serializers.ChoiceField(choices=ProposalActionType.choices)
    name = serializers.CharField(allow_blank=True)
    is_new = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
    files = ProposalFileSerializer(many=True)
    conversations = ProposalConversationSerializer(many=True)
    # Plans recorded before agents could be proposed have no `agent` key.
    agent = ProposalAgentSerializer(allow_null=True, default=None)
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
    model_choices = serializers.DictField(
        child=serializers.CharField(max_length=512),
        required=False,
        help_text="Apply only: create_agent action id -> the offered model picked.",
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


class AssistantThreadSummarySerializer(serializers.Serializer):
    """One past conversation in the history menu."""

    id = serializers.IntegerField()
    title = serializers.CharField(allow_null=True)
    last_message_at = serializers.DateTimeField()
    is_open = serializers.SerializerMethodField()

    def get_is_open(self, thread) -> bool:
        return thread.closed_at is None


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
