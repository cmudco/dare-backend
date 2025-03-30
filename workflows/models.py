from django.db import models
from django.conf import settings

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from conversations.models import LLM
from files.models import File
from prompts.models import Prompt
from workflows.constants import Mode

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
    file = models.ForeignKey(
        File,
        on_delete=models.SET_NULL,
        related_name="workflow_steps",
        null=True,
        blank=True,
        help_text="Optional file associated with this step."
    )
    llm = models.ForeignKey(
        LLM,
        on_delete=models.SET_NULL,
        related_name="workflow_steps",
        null=True,
        blank=True,
        help_text="Optional language model for this step."
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
