from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

from common.models import TimeStampMixin


class WorkflowNode(TimeStampMixin):
    """
    Model to store complete React Flow Node data with type-safe node data.

    This model maps directly to React Flow's Node interface and provides
    type-safe storage for different node types through generic foreign keys.

    Attributes:
        workflow: Parent workflow
        node_id: Unique identifier for React Flow
        data_object: Type-safe node data (StepNodeData, StartNodeData, etc.)

    Maps to React Flow Node interface: https://reactflow.dev/docs/api/nodes/node-options
    """
    workflow = models.ForeignKey(
        'Workflow',
        on_delete=models.CASCADE,
        related_name='nodes',
        help_text="Workflow this node belongs to"
    )

    # Core React Flow Node Properties
    node_id = models.CharField(
        max_length=255,
        help_text="Unique identifier for React Flow node (node.id)"
    )
    node_type = models.CharField(
        max_length=100,
        help_text="React Flow node type (node.type): step, start, chatOutput, conditional"
    )

    # Position Properties
    position_x = models.FloatField(help_text="X coordinate (node.position.x)")
    position_y = models.FloatField(help_text="Y coordinate (node.position.y)")
    width = models.FloatField(
        null=True,
        blank=True,
        help_text="Node width (calculated by React Flow, read-only)"
    )
    height = models.FloatField(
        null=True,
        blank=True,
        help_text="Node height (calculated by React Flow, read-only)"
    )

    # State Properties
    selected = models.BooleanField(
        default=False,
        help_text="Selection state (node.selected)"
    )
    dragging = models.BooleanField(
        default=False,
        help_text="Current drag status (node.dragging)"
    )

    # Behavior Properties
    draggable = models.BooleanField(
        default=True,
        help_text="Can node be dragged (node.draggable)"
    )
    selectable = models.BooleanField(
        default=True,
        help_text="Can node be selected (node.selectable)"
    )
    connectable = models.BooleanField(
        default=True,
        help_text="Can node have connections (node.connectable)"
    )
    deletable = models.BooleanField(
        default=True,
        help_text="Can node be deleted (node.deletable)"
    )
    hidden = models.BooleanField(
        default=False,
        help_text="Node visibility (node.hidden)"
    )

    # Connection Properties
    source_position = models.CharField(
        max_length=20,
        blank=True,
        help_text="Controls source connection point (node.sourcePosition): top/bottom/left/right"
    )
    target_position = models.CharField(
        max_length=20,
        blank=True,
        help_text="Controls target connection point (node.targetPosition): top/bottom/left/right"
    )

    # Hierarchy Properties
    parent_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Parent node for sub-flows (node.parentId)"
    )
    z_index = models.IntegerField(
        default=0,
        help_text="Rendering layer (node.zIndex)"
    )

    # Interaction Properties
    drag_handle = models.CharField(
        max_length=255,
        blank=True,
        help_text="CSS class for drag handles (node.dragHandle)"
    )

    # Styling Properties
    style = models.JSONField(
        default=dict,
        blank=True,
        help_text="CSS properties for node styling (node.style)"
    )
    class_name = models.CharField(
        max_length=500,
        blank=True,
        help_text="CSS class names (node.className)"
    )

    # Type-safe data relationship (replaces JSONField data)
    data_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        help_text="Content type of the associated node data"
    )
    data_object_id = models.PositiveIntegerField(
        help_text="ID of the associated node data object"
    )
    data_object = GenericForeignKey('data_content_type', 'data_object_id')

    class Meta:
        unique_together = ['workflow', 'node_id']
        ordering = ['node_id']
        indexes = [
            models.Index(fields=['node_type'], name='wf_node_type_idx'),
        ]

    @property
    def data(self):
        """Get node data as dict for API compatibility."""
        if self.data_object:
            return self.data_object.to_dict()
        return {}

    @property
    def typed_data(self):
        """Get properly typed data object."""
        return self.data_object

    def __str__(self):
        return f"Node {self.node_id} ({self.node_type}) in {self.workflow.title}"


class WorkflowEdge(TimeStampMixin):
    """
    Model to store complete React Flow Edge data.
    Maps to React Flow Edge interface: https://reactflow.dev/docs/api/edges/edge-options
    """
    workflow = models.ForeignKey(
        'Workflow',
        on_delete=models.CASCADE,
        related_name='edges',
        help_text="Workflow this edge belongs to"
    )

    # Core React Flow Edge Properties
    edge_id = models.CharField(
        max_length=255,
        help_text="Unique identifier for React Flow edge (edge.id)"
    )
    edge_type = models.CharField(
        max_length=100,
        default='default',
        help_text="Edge type: default/straight/step/smoothstep/simplebezier (edge.type)"
    )

    # Connection Properties
    source = models.CharField(
        max_length=255,
        help_text="Source node ID (edge.source)"
    )
    target = models.CharField(
        max_length=255,
        help_text="Target node ID (edge.target)"
    )
    source_handle = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Source handle ID (edge.sourceHandle)"
    )
    target_handle = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Target handle ID (edge.targetHandle)"
    )

    # Data & State Properties
    data = models.JSONField(
        default=dict,
        help_text="Arbitrary edge data (edge.data)"
    )
    selected = models.BooleanField(
        default=False,
        help_text="Selection state (edge.selected)"
    )

    # Behavior Properties
    animated = models.BooleanField(
        default=False,
        help_text="Animation state (edge.animated)"
    )
    hidden = models.BooleanField(
        default=False,
        help_text="Edge visibility (edge.hidden)"
    )
    deletable = models.BooleanField(
        default=True,
        help_text="Can edge be deleted (edge.deletable)"
    )
    selectable = models.BooleanField(
        default=True,
        help_text="Can edge be selected (edge.selectable)"
    )

    # Rendering Properties
    z_index = models.IntegerField(
        default=0,
        help_text="Rendering layer (edge.zIndex)"
    )
    label = models.TextField(
        blank=True,
        help_text="Edge label text (edge.label)"
    )

    # Styling Properties
    style = models.JSONField(
        default=dict,
        blank=True,
        help_text="CSS properties for edge styling (edge.style)"
    )
    class_name = models.CharField(
        max_length=500,
        blank=True,
        help_text="CSS class names (edge.className)"
    )

    # Marker Properties
    marker_start = models.JSONField(
        default=dict,
        blank=True,
        help_text="Start marker configuration (edge.markerStart)"
    )
    marker_end = models.JSONField(
        default=dict,
        blank=True,
        help_text="End marker configuration (edge.markerEnd)"
    )

    # Path Options (for smoothstep/bezier edges)
    path_options = models.JSONField(
        default=dict,
        blank=True,
        help_text="Path configuration for smoothstep/bezier edges (edge.pathOptions)"
    )

    class Meta:
        unique_together = ['workflow', 'edge_id']
        ordering = ['edge_id']

    def __str__(self):
        return f"Edge {self.edge_id} ({self.source} → {self.target}) in {self.workflow.title}"