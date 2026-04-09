from django.db import models
from django.conf import settings

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from files.models import File
from workflows.constants import Mode, WorkflowRunStepStatus, BatchRunStatus


class Workflow(BaseModel):
    """
    Container model for workflow nodes and edges with version control.

    This model serves as the main container for workflow components using a
    graph-based architecture. Actual workflow metadata (title, description, mode)
    is stored in StartNodeData, while step configuration is stored in StepNodeData
    via WorkflowNode relationships.

    Attributes:
        user: Owner of the workflow
        version: Version number for workflow iterations
        parent: Original workflow if this is a cloned version
        viewport_x/y/zoom: React Flow viewport state for UI positioning
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflows",
        help_text="User who owns this workflow"
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
        help_text="Original workflow this was cloned from"
    )

    # Viewport as simple fields (no JSON needed)
    viewport_x = models.FloatField(
        default=0.0,
        help_text="Viewport X position"
    )
    viewport_y = models.FloatField(
        default=0.0,
        help_text="Viewport Y position"
    )
    viewport_zoom = models.FloatField(
        default=1.0,
        help_text="Viewport zoom level"
    )
    manual_mode_enabled = models.BooleanField(
        default=False,
        help_text="Whether manual mode (step-by-step execution) is enabled for this workflow"
    )
    output_display_mode = models.CharField(
        max_length=10,
        choices=[('panel', 'Panel'), ('nodes', 'Nodes')],
        default='panel',
        help_text="Where to display workflow output: 'panel' (execution panel) or 'nodes' (output nodes)"
    )
    display_order = models.PositiveIntegerField(
        default=0,
        help_text="Order in which workflows are displayed in the UI. Higher values appear later."
    )

    # Publishing / sharing fields
    is_published = models.BooleanField(
        default=False,
        help_text="Whether this workflow is published and visible in the shared library"
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the workflow was published"
    )

    # Stored FK to the root start node (the one with no incoming edges).
    # Set via resolve_root_start_node() after nodes/edges are created or updated.
    root_start_node = models.ForeignKey(
        'workflows.WorkflowNode',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        help_text="Root start node (no incoming edges). Holds workflow title/description/mode."
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['display_order', '-created_at']

    def __str__(self):
        start_node = self.root_start_node
        if start_node and start_node.typed_data:
            title = start_node.typed_data.title
        else:
            title = 'Untitled'
        return f"{title} ({self.user.email})"

    def resolve_root_start_node(self) -> None:
        """Find the root start node (no incoming edges) and persist the FK."""
        start_nodes = list(self.nodes.filter(node_type='start'))
        if not start_nodes:
            root = None
        elif len(start_nodes) == 1:
            root = start_nodes[0]
        else:
            edge_targets = set(self.edges.values_list('target', flat=True))
            root = next(
                (sn for sn in start_nodes if sn.node_id not in edge_targets),
                None,
            )
        self.root_start_node = root
        self.save(update_fields=['root_start_node'])

    @property
    def title(self):
        """Get workflow title from root StartNodeData."""
        start_node = self.root_start_node
        if start_node and start_node.typed_data:
            return start_node.typed_data.title
        return ''

    @property
    def description(self):
        """Get workflow description from root StartNodeData."""
        start_node = self.root_start_node
        if start_node and start_node.typed_data:
            return start_node.typed_data.description
        return ''

    @property
    def mode(self):
        """Get workflow mode from root StartNodeData."""
        start_node = self.root_start_node
        if start_node and start_node.typed_data:
            return start_node.typed_data.mode
        return Mode.PARALLEL

    @property
    def step_nodes(self):
        """Get step nodes (order determined at execution time by node handlers)."""
        return self.nodes.filter(node_type='step')

    @property
    def viewport(self):
        """Get viewport as dict for API compatibility."""
        return {
            'x': self.viewport_x,
            'y': self.viewport_y,
            'zoom': self.viewport_zoom
        }


class BatchRun(BaseModel):
    """
    Represents a batch execution of a workflow across multiple files.
    """
    workflow = models.ForeignKey(
        'Workflow',
        on_delete=models.CASCADE,
        related_name='batch_runs',
        help_text="Workflow being executed in batch."
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workflow_batch_runs',
        help_text="User who initiated this batch run."
    )
    status = models.CharField(
        max_length=20,
        choices=BatchRunStatus.choices,
        default=BatchRunStatus.RUNNING,
        help_text="Batch execution status."
    )
    total_files = models.PositiveIntegerField(
        default=0,
        help_text="Total number of files in this batch."
    )
    completed_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of completed workflow runs in this batch."
    )
    failed_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of failed workflow runs in this batch."
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the batch finished."
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    @property
    def started_at(self):
        return self.created_at

    def __str__(self):
        return (
            f"Batch {self.id} for {self.workflow.title} by {self.user.email} "
            f"({self.completed_count}/{self.total_files} completed)"
        )


class WorkflowRun(BaseModel):
    """
    Represents an instance of a workflow execution.
    """
    workflow = models.ForeignKey(
        'Workflow',
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
    status = models.CharField(
        max_length=20,
        choices=WorkflowRunStepStatus.choices,
        default=WorkflowRunStepStatus.RUNNING,
        help_text="Current status of the workflow run."
    )
    is_partial = models.BooleanField(
        default=False,
        help_text="Whether this is a partial run (manual step-by-step execution) or complete workflow run."
    )
    batch_run = models.ForeignKey(
        'BatchRun',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_runs',
        help_text="Batch run this workflow execution belongs to."
    )
    batch_file = models.ForeignKey(
        File,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='batch_workflow_runs',
        help_text="File injected into start-connected steps for this batch run."
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    @property
    def started_at(self):
        return self.created_at

    def __str__(self):
        run_type = "Partial run" if self.is_partial else "Run"
        return f"{run_type} of {self.workflow.title} by {self.user.email} at {self.created_at}"


class WorkflowRunStep(TimeStampMixin):
    """
    Represents the execution of a single step node within a workflow run.
    """
    workflow_run = models.ForeignKey(
        WorkflowRun,
        on_delete=models.CASCADE,
        related_name='steps',
        help_text="Workflow run this step belongs to."
    )
    step_node = models.ForeignKey(
        'WorkflowNode',
        on_delete=models.CASCADE,
        limit_choices_to={'node_type': 'step'},
        help_text="Step node being executed.",
        null=True  # Temporary for migration
    )
    order = models.PositiveIntegerField(
        help_text="Order of this step in the run."
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when this step started executing."
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
    metadata = models.JSONField(
        null=True,
        blank=True,
        default=dict,
        help_text="Additional metadata about step execution (e.g., AI analysis, routing decisions)"
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Step {self.order} of {self.workflow_run}"

    @property
    def step_data(self):
        """Get the StepNodeData from the associated step node."""
        if self.step_node and self.step_node.node_type == 'step':
            return self.step_node.data_object
        return None
