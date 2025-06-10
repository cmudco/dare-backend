from rest_framework import serializers
from conversations.api.serializers import LLMSerializer
from conversations.models import LLM
from files.api.serializers import FileSerializer
from files.models import File
from prompts.models import Prompt
from workflows.models import Workflow, Step, WorkflowRun, WorkflowRunStep
from workflows.constants import WorkflowRunStepStatus
from prompts.api.serializers import PromptSerializer

class WorkflowRunStepSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(
        choices=WorkflowRunStepStatus.choices,
        default=WorkflowRunStepStatus.PENDING
    )

    class Meta:
        model = WorkflowRunStep
        fields = ['id', 'step', 'order', 'status', 'response', 'error', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

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
    file = serializers.PrimaryKeyRelatedField(
        queryset=File.active_objects.all(),
        required=False,
        allow_null=True
    )
    llm = serializers.PrimaryKeyRelatedField(
        queryset=LLM.objects.all(),
        required=False,
        allow_null=True
    )
    max_tokens = serializers.IntegerField(required=False)
    temperature = serializers.FloatField(required=False)
    max_context_snippets = serializers.IntegerField(required=False)
    document_similarity_threshold = serializers.FloatField(required=False)
    is_embeddings = serializers.BooleanField(required=False)


    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['prompt'] = PromptSerializer(instance.prompt).data
        representation['file'] = FileSerializer(instance.file).data if instance.file else None
        representation['llm'] = LLMSerializer(instance.llm).data if instance.llm else None
        return representation

    class Meta:
        model = Step
        fields = [
            'id', 'prompt', 'file', 'llm', 'order', 'created_at', 'user',
            'max_tokens', 'temperature', 'max_context_snippets',
            'document_similarity_threshold', 'is_embeddings'
        ]
        read_only_fields = ['id', 'created_at', 'user']

class WorkflowSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.email')
    steps = StepSerializer(many=True, required=False)
    latest_run = serializers.SerializerMethodField()

    class Meta:
        model = Workflow
        fields = ['id', 'title', 'description', 'mode', 'created_at', 'user', 'steps', 'latest_run']
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
            step = Step.objects.create(
                user=workflow.user,
                prompt=step_data['prompt'],
                file=step_data.get('file'),
                llm=step_data.get('llm'),
                order=step_data['order'],
                max_tokens=step_data.get('max_tokens', Step._meta.get_field('max_tokens').default),
                temperature=step_data.get('temperature', Step._meta.get_field('temperature').default),
                max_context_snippets=step_data.get('max_context_snippets', Step._meta.get_field('max_context_snippets').default),
                document_similarity_threshold=step_data.get('document_similarity_threshold', Step._meta.get_field('document_similarity_threshold').default),
                is_embeddings=step_data.get('is_embeddings', Step._meta.get_field('is_embeddings').default)
            )
            workflow.steps.add(step)
        return workflow

    def update(self, instance, validated_data):
        steps_data = validated_data.pop('steps', [])

        instance.title = validated_data.get('title', instance.title)
        instance.description = validated_data.get('description', instance.description)
        instance.mode = validated_data.get('mode', instance.mode)
        instance.save()

        existing_steps = {step.id: step for step in instance.steps.all()}
        updated_step_ids = {step_data['id'] for step_data in steps_data if 'id' in step_data}

        for step_id, step in existing_steps.items():
            if step_id not in updated_step_ids:
                instance.steps.remove(step)
                step.delete()

        for step_data in steps_data:
            step_id = step_data.get('id')
            if step_id and step_id in existing_steps:
                step = existing_steps[step_id]
                step.prompt = step_data['prompt']
                step.file = step_data.get('file')
                step.llm = step_data.get('llm')
                step.order = step_data['order']
                step.max_tokens = step_data.get('max_tokens', Step._meta.get_field('max_tokens').default)
                step.temperature = step_data.get('temperature', Step._meta.get_field('temperature').default)
                step.max_context_snippets = step_data.get('max_context_snippets', Step._meta.get_field('max_context_snippets').default)
                step.document_similarity_threshold = step_data.get('document_similarity_threshold', Step._meta.get_field('document_similarity_threshold').default)
                step.is_embeddings = step_data.get('is_embeddings', Step._meta.get_field('is_embeddings').default)
                step.save()
            else:
                step = Step.objects.create(
                    user=instance.user,
                    prompt=step_data['prompt'],
                    file=step_data.get('file'),
                    llm=step_data.get('llm'),
                    order=step_data['order'],
                    max_tokens=step_data.get('max_tokens', Step._meta.get_field('max_tokens').default),
                    temperature=step_data.get('temperature', Step._meta.get_field('temperature').default),
                    max_context_snippets=step_data.get('max_context_snippets', Step._meta.get_field('max_context_snippets').default),
                    document_similarity_threshold=step_data.get('document_similarity_threshold', Step._meta.get_field('document_similarity_threshold').default),
                    is_embeddings=step_data.get('is_embeddings', Step._meta.get_field('is_embeddings').default)
                )
                instance.steps.add(step)

        return instance
