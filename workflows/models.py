from django.db import models
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MinValueValidator, MaxValueValidator

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from conversations.models import LLM
from files.models import File
from prompts.models import Prompt
from workflows.constants import Mode, WorkflowRunStepStatus


# ==========================================
# NEW GRAPH-DRIVEN ARCHITECTURE MODELS
# ==========================================

class BaseNodeData(models.Model):
    """Base class for type-safe node data storage."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def to_dict(self):
        """Convert to dict for API serialization. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement to_dict()")


class StepNodeData(BaseNodeData):
    """Data model for 'step' type nodes - replaces Step model entirely."""
    prompt = models.ForeignKey(
        'prompts.Prompt',
        on_delete=models.PROTECT,
        help_text="Prompt template for this step"
    )
    content_files = models.ManyToManyField(
        'files.File',
        related_name='step_node_content',
        blank=True,
        help_text="Files to be processed with full content"
    )
    embedding_files = models.ManyToManyField(
        'files.File',
        related_name='step_node_embeddings',
        blank=True,
        help_text="Files to be processed using embeddings/vector search"
    )
    llm = models.ForeignKey(
        'conversations.LLM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Language model for this step"
    )
    step_number = models.PositiveIntegerField(help_text="Step order in workflow")
    max_tokens = models.PositiveIntegerField(
        default=2048,
        help_text="Maximum tokens for LLM response"
    )
    temperature = models.FloatField(
        default=0.7,
        validators=[MinValueValidator(0.0), MaxValueValidator(2.0)],
        help_text="Temperature setting for the LLM"
    )
    max_context_snippets = models.PositiveIntegerField(
        default=4,
        help_text="Maximum number of context snippets to retrieve"
    )
    document_similarity_threshold = models.FloatField(
        default=0.2,
        help_text="Similarity threshold for document retrieval"
    )
    use_previous_step_files = models.BooleanField(
        default=False,
        help_text="Inherit files from previous step"
    )
    use_previous_step_embeddings = models.BooleanField(
        default=False,
        help_text="Inherit embeddings from previous step"
    )

    def to_dict(self):
        """Convert to React Flow node data format."""
        return {
            'prompt': self.prompt.id if self.prompt else None,
            'contentFiles': list(self.content_files.values_list('id', flat=True)),
            'embeddingFiles': list(self.embedding_files.values_list('id', flat=True)),
            'llm': self.llm.id if self.llm else None,
            'stepNumber': self.step_number,
            'maxTokens': self.max_tokens,
            'temperature': self.temperature,
            'maxContextSnippets': self.max_context_snippets,
            'documentSimilarityThreshold': self.document_similarity_threshold,
            'usePreviousStepFiles': self.use_previous_step_files,
            'usePreviousStepEmbeddings': self.use_previous_step_embeddings,
        }

    def __str__(self):
        return f"Step {self.step_number}: {self.prompt.title if self.prompt else 'No Prompt'}"


class StartNodeData(BaseNodeData):
    """Data model for 'start' type nodes."""
    title = models.CharField(
        max_length=255,
        help_text="Workflow title"
    )
    description = models.TextField(
        help_text="Workflow description"
    )
    mode = models.CharField(
        max_length=20,
        choices=[('sequential', 'Sequential'), ('parallel', 'Parallel')],
        default='sequential',
        help_text="Workflow execution mode"
    )

    def to_dict(self):
        return {
            'title': self.title,
            'description': self.description,
            'mode': self.mode,
        }

    def __str__(self):
        return f"Start: {self.title}"


class ChatOutputNodeData(BaseNodeData):
    """Data model for 'chatOutput' type nodes."""
    step_number = models.PositiveIntegerField(
        help_text="Associated step number for output"
    )
    status = models.CharField(
        max_length=20,
        blank=True,
        help_text="Execution status"
    )
    response = models.TextField(
        blank=True,
        help_text="Step execution response"
    )
    error = models.TextField(
        blank=True,
        help_text="Error message if step failed"
    )

    def to_dict(self):
        return {
            'stepNumber': self.step_number,
            'status': self.status,
            'response': self.response,
            'error': self.error,
        }

    def __str__(self):
        return f"Output for Step {self.step_number}"


class AggregatorNodeData(BaseNodeData):
    """Data model for 'aggregator' type nodes."""
    scoring_mode = models.CharField(
        max_length=20,
        choices=[('quantitative', 'Quantitative'), ('qualitative', 'Qualitative')],
        default='quantitative',
        help_text="Scoring mode: quantitative (0-100) or qualitative (true/false)"
    )
    custom_prompt = models.TextField(
        default='Evaluate the quality of the responses and provide a score based on accuracy, relevance, and clarity.',
        help_text="Custom evaluation prompt for the aggregator"
    )
    step_number = models.PositiveIntegerField(
        help_text="Step number for this aggregator node"
    )

    def to_dict(self):
        return {
            'scoringMode': self.scoring_mode,
            'customPrompt': self.custom_prompt,
            'stepNumber': self.step_number,
        }

    def __str__(self):
        return f"Aggregator {self.step_number}: {self.scoring_mode}"


class WorkflowNode(TimeStampMixin):
    """
    Model to store complete React Flow Node data with type-safe node data.
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
        help_text="React Flow node type (node.type): step, start, chatOutput, aggregator"
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


# ==========================================
# LEGACY MODELS (TO BE DEPRECATED)
# ==========================================
# Step model removed - replaced by StepNodeData in graph-driven architecture


class Workflow(BaseModel):
    """
    Minimal workflow model - metadata now stored in StartNodeData.
    Step execution data stored in StepNodeData via WorkflowNode relationship.
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

    active_objects = ActiveObjectsManager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        # Get title from StartNodeData
        start_node = self.nodes.filter(node_type='start').first()
        if start_node and start_node.typed_data:
            title = start_node.typed_data.title
        else:
            title = 'Untitled'
        return f"{title} ({self.user.email})"

    @property
    def title(self):
        """Get workflow title from StartNodeData."""
        start_node = self.nodes.filter(node_type='start').first()
        if start_node and start_node.typed_data:
            return start_node.typed_data.title
        return ''

    @property
    def description(self):
        """Get workflow description from StartNodeData."""
        start_node = self.nodes.filter(node_type='start').first()
        if start_node and start_node.typed_data:
            return start_node.typed_data.description
        return ''

    @property
    def mode(self):
        """Get workflow mode from StartNodeData."""
        start_node = self.nodes.filter(node_type='start').first()
        if start_node and start_node.typed_data:
            mode_str = start_node.typed_data.mode
            return 2 if mode_str == 'parallel' else 1
        return 1

    @property
    def step_nodes(self):
        """Get ordered step nodes from graph."""
        return self.nodes.filter(node_type='step').order_by('data_object__step_number')

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
    Represents the execution of a single step node within a workflow run.
    """
    workflow_run = models.ForeignKey(
        WorkflowRun,
        on_delete=models.CASCADE,
        related_name='steps',
        help_text="Workflow run this step belongs to."
    )
    step_node = models.ForeignKey(
        WorkflowNode,
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