from rest_framework import serializers
from conversations.api.serializers import LLMSerializer
from conversations.models import LLM
from files.api.serializers import FileSerializer
from files.models import File
from prompts.models import Prompt
from workflows.models import Workflow, Step, WorkflowRun, WorkflowRunStep, WorkflowStepSnippet
from workflows.constants import WorkflowRunStepStatus
from prompts.api.serializers import PromptSerializer


def _merge_step_ids_from_initial_data(steps_data, initial_data):
    """
    Merge step IDs from initial_data back into validated steps_data.
    This is needed because DRF validation strips IDs from nested serializers.
    """
    raw_steps = initial_data.get('steps', []) if initial_data else []
    for i, step_data in enumerate(steps_data):
        if i < len(raw_steps) and 'id' in raw_steps[i]:
            step_data['id'] = raw_steps[i]['id']


class WorkflowStepSnippetSerializer(serializers.ModelSerializer):
    file = FileSerializer(read_only=True)
    vector_db_source = serializers.CharField(read_only=True)

    class Meta:
        model = WorkflowStepSnippet
        fields = ['id', 'file', 'text', 'similarity_score', 'chunk_index', 'vector_db_source']


class WorkflowRunStepSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(
        choices=WorkflowRunStepStatus.choices,
        default=WorkflowRunStepStatus.PENDING
    )
    snippets = WorkflowStepSnippetSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowRunStep
        fields = ['id', 'step', 'order', 'status', 'response', 'error', 'created_at', 'updated_at', 'snippets']
        read_only_fields = ['id', 'created_at', 'updated_at', 'snippets']

class WorkflowRunSerializer(serializers.ModelSerializer):
    steps = WorkflowRunStepSerializer(many=True, read_only=True)
    started_at = serializers.DateTimeField()
    status = serializers.CharField()
    workflow_title = serializers.SerializerMethodField()
    workflow_description = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowRun
        fields = ['id', 'workflow', 'user', 'started_at', 'ended_at', 'status', 'steps', 'workflow_title', 'workflow_description']
        read_only_fields = ['id', 'started_at', 'ended_at', 'status', 'steps', 'workflow_title', 'workflow_description']

    def get_workflow_title(self, obj):
        return obj.workflow.title if obj.workflow else None

    def get_workflow_description(self, obj):
        return obj.workflow.description if obj.workflow else None

class StepSerializer(serializers.ModelSerializer):
    prompt = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        required=True
    )
    files = serializers.PrimaryKeyRelatedField(
        queryset=File.active_objects.all(),
        many=True,
        required=False,
        allow_empty=True
    )
    embeddings = serializers.PrimaryKeyRelatedField(
        queryset=File.active_objects.all(),
        many=True,
        required=False,
        allow_empty=True
    )
    use_previous_step_files = serializers.BooleanField(required=False)
    use_previous_step_embeddings = serializers.BooleanField(required=False)
    llm = serializers.PrimaryKeyRelatedField(
        queryset=LLM.objects.all(),
        required=False,
        allow_null=True
    )
    max_tokens = serializers.IntegerField(required=False)
    temperature = serializers.FloatField(required=False)
    max_context_snippets = serializers.IntegerField(required=False)
    document_similarity_threshold = serializers.FloatField(required=False)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        try:
            representation['prompt'] = PromptSerializer(instance.prompt).data
        except:
            representation['prompt'] = None
        representation['files'] = FileSerializer(instance.files.all(), many=True).data
        representation['embeddings'] = FileSerializer(instance.embeddings.all(), many=True).data
        representation['llm'] = LLMSerializer(instance.llm).data if instance.llm else None
        return representation

    class Meta:
        model = Step
        fields = [
            'id', 'prompt', 'files', 'embeddings', 'use_previous_step_files',
            'use_previous_step_embeddings', 'llm', 'order', 'created_at', 'user',
            'max_tokens', 'temperature', 'max_context_snippets',
            'document_similarity_threshold'
        ]
        read_only_fields = ['created_at', 'user']


class WorkflowSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.email')
    steps = StepSerializer(many=True, required=False)
    latest_run = serializers.SerializerMethodField()
    layout = serializers.JSONField(required=False)
    viewport = serializers.JSONField(required=False, allow_null=True)

    class Meta:
        model = Workflow
        fields = ['id', 'title', 'description', 'mode', 'version', 'parent', 'created_at', 'user', 'steps', 'latest_run', 'layout', 'viewport']
        read_only_fields = ['id', 'created_at', 'user', 'steps_detail', 'latest_run']

    def get_latest_run(self, obj):
        latest_run = WorkflowRun.active_objects.filter(workflow=obj).order_by('-created_at').first()
        if latest_run:
            return WorkflowRunSerializer(latest_run).data
        return None

    def create(self, validated_data):
        steps_data = validated_data.pop('steps', [])
        workflow = Workflow.active_objects.create(**validated_data)

        for step_data in steps_data:
            files_data = step_data.pop('files', [])
            embeddings_data = step_data.pop('embeddings', [])
            step = Step.objects.create(
                user=workflow.user,
                **step_data
            )
            step.files.set(files_data)
            step.embeddings.set(embeddings_data)
            workflow.steps.add(step)
        return workflow

    def update(self, instance, validated_data):
        steps_data = validated_data.pop('steps', [])

        # Merge IDs from initial_data back into validated steps_data
        _merge_step_ids_from_initial_data(steps_data, getattr(self, 'initial_data', None))

        # Update workflow fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Handle steps
        existing_steps = {step.id: step for step in instance.steps.all()}
        updated_step_ids = {step_data.get('id') for step_data in steps_data if step_data.get('id')}

        for step_id, step in existing_steps.items():
            if step_id not in updated_step_ids:
                instance.steps.remove(step)
                step.delete()

        # Update or create steps
        for step_data in steps_data:
            files_data = step_data.pop('files', [])
            embeddings_data = step_data.pop('embeddings', [])
            step_id = step_data.get('id')

            if step_id and step_id in existing_steps:
                # Update existing step
                step = existing_steps[step_id]
                for attr, value in step_data.items():
                    setattr(step, attr, value)
                step.save()
                step.files.set(files_data)
                step.embeddings.set(embeddings_data)
            else:
                # Create new step
                step = Step.objects.create(
                    user=instance.user,
                    **step_data
                )
                step.files.set(files_data)
                step.embeddings.set(embeddings_data)
                instance.steps.add(step)

        return instance
