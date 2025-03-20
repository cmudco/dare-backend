from rest_framework import serializers
from workflows.models import Workflow, Step
from prompts.api.serializers import PromptSerializer

class StepSerializer(serializers.ModelSerializer):
    prompt_detail = PromptSerializer(source='prompt', read_only=True)

    class Meta:
        model = Step
        fields = ['id', 'prompt', 'prompt_detail', 'order', 'created_at', 'user']
        read_only_fields = ['id', 'created_at']

class WorkflowSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.email')
    steps_detail = StepSerializer(source='steps', many=True, read_only=True)
    steps_ids = serializers.PrimaryKeyRelatedField(
        queryset=Step.objects.all(),
        many=True,
        write_only=True,
        required=False,
        source='steps'
    )

    class Meta:
        model = Workflow
        fields = ['id', 'title', 'description', 'mode', 'created_at', 'user', 'steps_detail', 'steps_ids']
        read_only_fields = ['id', 'created_at', 'user', 'steps_detail']