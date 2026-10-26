from rest_framework import serializers

from core.constants import BackgroundModelSource, BackgroundTask


class BackgroundModelCandidateSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    name = serializers.CharField()
    provider = serializers.CharField()
    input_token_rate_per_million = serializers.DecimalField(
        max_digits=12, decimal_places=4
    )
    output_token_rate_per_million = serializers.DecimalField(
        max_digits=12, decimal_places=4
    )


class _SourceLabelMixin(serializers.Serializer):
    source = serializers.CharField()
    source_label = serializers.SerializerMethodField()

    def get_source_label(self, obj) -> str:
        return str(BackgroundModelSource(obj.source).label)


class BackgroundTaskModelSerializer(_SourceLabelMixin):
    task = serializers.CharField()
    label = serializers.CharField()
    model_identifier = serializers.CharField()
    model_name = serializers.CharField()
    wallet_type = serializers.CharField()


class LiteLLMKeySummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    background_model = serializers.CharField()


class BackgroundModelOverviewSerializer(serializers.Serializer):
    wallet_type = serializers.CharField()
    litellm_key = LiteLLMKeySummarySerializer(allow_null=True)
    selected = serializers.CharField()
    selected_offered = serializers.BooleanField()
    tasks = BackgroundTaskModelSerializer(many=True)
    candidates = BackgroundModelCandidateSerializer(many=True)
    can_manage_platform = serializers.BooleanField()


class BackgroundModelSelectionSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=255, allow_blank=True)


class PlatformTaskModelSerializer(_SourceLabelMixin):
    task = serializers.CharField()
    label = serializers.CharField()
    override = serializers.CharField()
    in_use = BackgroundModelCandidateSerializer(allow_null=True)


class PlatformBackgroundModelsSerializer(serializers.Serializer):
    default = serializers.CharField()
    tasks = PlatformTaskModelSerializer(many=True)
    candidates = BackgroundModelCandidateSerializer(many=True)


class TaskOverrideSerializer(serializers.Serializer):
    task = serializers.ChoiceField(choices=BackgroundTask.choices)
    model = serializers.CharField(max_length=255, allow_blank=True)


class PlatformBackgroundModelsUpdateSerializer(serializers.Serializer):
    default = serializers.CharField(max_length=255, allow_blank=True, required=False)
    overrides = TaskOverrideSerializer(many=True, required=False)
