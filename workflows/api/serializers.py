from rest_framework import serializers
from prompts.models import Prompt
from workflows.models import Workflow, Step
from prompts.api.serializers import PromptSerializer

class StepSerializer(serializers.ModelSerializer):
    prompt = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        required=True
    )

    def to_representation(self, instance):
        # Override to return full Prompt object in responses
        representation = super().to_representation(instance)
        representation['prompt'] = PromptSerializer(instance.prompt).data
        return representation

    class Meta:
        model = Step
        fields = ['id', 'prompt', 'order', 'created_at', 'user']
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