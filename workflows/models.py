from django.db import models
from django.conf import settings

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from conversations.models import LLM
from files.models import File
from prompts.models import Prompt
from workflows.constants import Mode, WorkflowRunStepStatus

class Step(TimeStampMixin):
    """
    Model for reusable workflow steps.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="steps",
        help_text="User who owns this step."
    )
    prompt = models.ForeignKey(
        Prompt,
        on_delete=models.CASCADE,
        related_name="workflow_steps",
        help_text="Prompt associated with this step."
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Default order of the step."
    )
    files = models.ManyToManyField(
        File,
        related_name="workflow_steps_files",
        blank=True,
        help_text="Files to be processed with full content."
    )
    embeddings = models.ManyToManyField(
        File,
        related_name="workflow_steps_embeddings",
        blank=True,
        help_text="Files to be processed using embeddings/vector search."
    )
    llm = models.ForeignKey(
        LLM,
        on_delete=models.SET_NULL,
        related_name="workflow_steps",
        null=True,
        blank=True,
        help_text="Optional language model for this step."
    )
    max_tokens = models.PositiveIntegerField(
        default=2048,
        help_text="Maximum tokens for LLM response for this step."
    )
    temperature = models.FloatField(
        default=0.7,
        help_text="Temperature setting for the LLM for this step."
    )
    max_context_snippets = models.PositiveIntegerField(
        default=4,
        help_text="Maximum number of context snippets to retrieve for this step."
    )
    document_similarity_threshold = models.FloatField(
        default=0.2,
        help_text="Similarity threshold for document retrieval in this step."
    )

    objects = models.Manager()

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Step {self.order}: {self.prompt.title}"


class Workflow(BaseModel):
    """
    Model for user workflows that can be saved and reused.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflows",
        help_text="User who owns this workflow."
    )
    title = models.CharField(
        max_length=255,
        help_text="Title of the workflow."
    )
    description = models.TextField(
        help_text="Description of the workflow."
    )
    mode = models.IntegerField(
        choices=Mode.choices,
        default=Mode.SERIAL,
        help_text="Mode of operation (Serial or Parallel)."
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number of the workflow. Increments when cloned."
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
        help_text="Original workflow this was cloned from."
    )
    steps = models.ManyToManyField(
        Step,
        related_name='workflows',
        blank=True,
        help_text="Steps included in this workflow"
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.user.email})"

class WorkflowRun(BaseModel):
    """
    Represents an instance of a workflow execution.
    """
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name='runs',
        help_text="Workflow being executed."
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workflow_runs',
        help_text="User who initiated this run."
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the run ended."
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    @property
    def started_at(self):
        return self.created_at

    @property
    def status(self):
        steps = self.steps.all()
        if not steps:
            return WorkflowRunStepStatus.RUNNING
        if any(step.status == WorkflowRunStepStatus.FAILED for step in steps):
            return WorkflowRunStepStatus.FAILED
        if all(step.status == WorkflowRunStepStatus.COMPLETED for step in steps):
            return WorkflowRunStepStatus.COMPLETED
        return WorkflowRunStepStatus.RUNNING

    def __str__(self):
        return f"Run of {self.workflow.title} by {self.user.email} at {self.created_at}"

class WorkflowRunStep(TimeStampMixin):
    """
    Represents the execution of a single step within a workflow run.
    """
    workflow_run = models.ForeignKey(
        WorkflowRun,
        on_delete=models.CASCADE,
        related_name='steps',
        help_text="Workflow run this step belongs to."
    )
    step = models.ForeignKey(
        Step,
        on_delete=models.CASCADE,
        help_text="Step being executed."
    )
    order = models.PositiveIntegerField(
        help_text="Order of this step in the run."
    )
    status = models.CharField(
        max_length=20,
        choices=WorkflowRunStepStatus.choices,
        default=WorkflowRunStepStatus.PENDING,
        help_text="Current status of this step."
    )
    response = models.TextField(
        null=True,
        blank=True,
        help_text="Response from step execution."
    )
    error = models.TextField(
        null=True,
        blank=True,
        help_text="Error message if step failed."
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Step {self.order} of {self.workflow_run}"


class WorkflowStepSnippet(BaseModel):
    """
    Model to track retrieved snippets from vector search for workflow steps.
    """
    workflow_run_step = models.ForeignKey(
        WorkflowRunStep,
        on_delete=models.CASCADE,
        related_name="snippets",
        help_text="The workflow run step this snippet was retrieved for."
    )
    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="workflow_step_snippets",
        help_text="The file this snippet belongs to."
    )
    text = models.TextField(
        help_text="The text content of the snippet (chunk)."
    )
    similarity_score = models.FloatField(
        help_text="The similarity score of the snippet to the query."
    )
    chunk_index = models.PositiveIntegerField(
        help_text="The index of the chunk in the original file."
    )
    vector_db_source = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="The vector database source (e.g., 'pinecone', 'weaviate')."
    )

    active_objects = ActiveObjectsManager()

    def __str__(self):
        return f"Snippet for WorkflowRunStep {self.workflow_run_step.id} from File {self.file.id} (Score: {self.similarity_score})"