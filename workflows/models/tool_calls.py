"""
Workflow Tool Call Model

Tool calls made by a workflow step's LLM turn through the shared tool loop.
Mirrors conversations.MessageToolCall for Message, so both surfaces render
the same tool-call history shape.
"""

from django.db import models

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from conversations.constants import ToolCallOrigin


class WorkflowStepToolCall(BaseModel):
    """
    Tracks tool calls within a workflow step execution.
    """

    workflow_run_step = models.ForeignKey(
        "workflows.WorkflowRunStep",
        on_delete=models.CASCADE,
        related_name="tool_calls",
        help_text="The workflow run step whose LLM turn requested this tool call.",
    )
    tool_call_id = models.CharField(
        max_length=100,
        help_text="Unique ID from LLM (e.g., 'call_abc123' or 'toolu_abc123').",
    )
    server_slug = models.CharField(
        max_length=100,
        help_text="Concrete tool server/provider slug (e.g., 'dare', 'slack').",
    )
    origin = models.CharField(
        max_length=20,
        choices=ToolCallOrigin.choices,
        default=ToolCallOrigin.MCP,
        help_text="Execution origin: DARE internal, MCP external, or provider-native.",
    )
    tool_name = models.CharField(
        max_length=200, help_text="Name of the tool (e.g., 'search_documents')."
    )
    arguments = models.JSONField(
        default=dict, help_text="Arguments passed to the tool."
    )
    round_index = models.PositiveSmallIntegerField(
        default=0, help_text="Tool-loop round this call executed in (1-based)."
    )
    status = models.CharField(
        max_length=30,
        choices=[
            ("pending", "Pending"),
            ("executing", "Executing"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        default="pending",
        help_text="Current status of the tool call.",
    )
    result = models.TextField(
        null=True, blank=True, help_text="Bounded JSON result text from tool execution."
    )
    error = models.TextField(
        null=True, blank=True, help_text="Error message if execution failed."
    )
    executed_at = models.DateTimeField(
        null=True, blank=True, help_text="When the tool was executed."
    )
    execution_time_ms = models.PositiveIntegerField(
        default=0,
        help_text="Wall-clock execution time of the tool call in milliseconds.",
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["workflow_run_step", "status"], name="wstc_step_status_idx"
            ),
            models.Index(fields=["tool_call_id"], name="wstc_call_id_idx"),
        ]

    def __str__(self):
        return f"{self.server_slug}.{self.tool_name} ({self.status}) for step {self.workflow_run_step_id}"
