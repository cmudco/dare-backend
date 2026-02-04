from django.db import models
from django.conf import settings

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from workflows.constants import WorkflowRunStepStatus


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

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['display_order', '-created_at']

    def __str__(self):
        # Get title from StartNodeData
        start_node = self.nodes.filter(node_type='start').first()
        if start_node and start_node.typed_data:
            title = start_node.typed_data.title
        else:
            title = 'Untitled'
        return f"{title} ({self.user.email})"

    def _get_root_start_node(self):
        """
        Get the root start node - the one with NO incoming edges.
        
        Chained start nodes have incoming edges from chatOutput nodes.
        The root start node (which holds the workflow title) has no incoming edges.
        """
        start_nodes = list(self.nodes.filter(node_type='start'))
        if not start_nodes:
            return None
        
        if len(start_nodes) == 1:
            return start_nodes[0]
        
        # Multiple start nodes - find the one with no incoming edges
        edge_targets = set(self.edges.values_list('target', flat=True))
        
        for start_node in start_nodes:
            if start_node.node_id not in edge_targets:
                return start_node
        
        # Fallback: use first one
        return start_nodes[0]

    @property
    def title(self):
        """Get workflow title from root StartNodeData (no incoming edges)."""
        start_node = self._get_root_start_node()
        if start_node and start_node.typed_data:
            return start_node.typed_data.title
        return ''

    @property
    def description(self):
        """Get workflow description from root StartNodeData."""
        start_node = self._get_root_start_node()
        if start_node and start_node.typed_data:
            return start_node.typed_data.description
        return ''

    @property
    def mode(self):
        """Get workflow mode from root StartNodeData."""
        start_node = self._get_root_start_node()
        if start_node and start_node.typed_data:
            mode_str = start_node.typed_data.mode
            return 2 if mode_str == 'parallel' else 1
        return 1

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
    is_partial = models.BooleanField(
        default=False,
        help_text="Whether this is a partial run (manual step-by-step execution) or complete workflow run."
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
        
        # Check if any step is waiting for human input - this takes precedence
        if any(step.status == WorkflowRunStepStatus.PENDING_HUMAN_INPUT for step in steps):
            return WorkflowRunStepStatus.PENDING_HUMAN_INPUT
        
        # Check for failures
        if any(step.status == WorkflowRunStepStatus.FAILED for step in steps):
            return WorkflowRunStepStatus.FAILED
        
        # Consider both COMPLETED and SKIPPED as finished states
        finished_statuses = {WorkflowRunStepStatus.COMPLETED, WorkflowRunStepStatus.SKIPPED}
        if all(step.status in finished_statuses for step in steps):
            return WorkflowRunStepStatus.COMPLETED
        
        return WorkflowRunStepStatus.RUNNING

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