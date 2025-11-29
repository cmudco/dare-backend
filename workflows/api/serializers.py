import logging

from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from conversations.models import LLM
from files.api.serializers import FileSerializer
from prompts.models import Prompt
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.utils import MetadataKey
from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep,  # WorkflowStepSnippet,
    # Graph-driven models
    StepNodeData, StartNodeData, ChatOutputNodeData, ConditionalNodeData, StructuredOutputNodeData,
    WorkflowNode, WorkflowEdge
)
from workflows.services import NodeExecutionStateBuilder
from workflows.utils import convert_keys_to_snake_case


logger = logging.getLogger(__name__)


# TEMPORARILY COMMENTED OUT - TABLE MISSING
# class WorkflowStepSnippetSerializer(serializers.ModelSerializer):
#     file = FileSerializer(read_only=True)
#     vector_db_source = serializers.CharField(read_only=True)

#     class Meta:
#         model = WorkflowStepSnippet
#         fields = ['id', 'file', 'text', 'similarity_score', 'chunk_index', 'vector_db_source']


class WorkflowRunStepSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(
        choices=WorkflowRunStepStatus.choices,
        default=WorkflowRunStepStatus.PENDING
    )
    # snippets = WorkflowStepSnippetSerializer(many=True, read_only=True)  # TEMPORARILY COMMENTED

    class Meta:
        model = WorkflowRunStep
        fields = ['id', 'step_node', 'order', 'status', 'response', 'error', 'metadata', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

class WorkflowRunSerializer(serializers.ModelSerializer):
    steps = WorkflowRunStepSerializer(many=True, read_only=True)
    started_at = serializers.DateTimeField()
    status = serializers.CharField()
    workflow_title = serializers.SerializerMethodField()
    workflow_description = serializers.SerializerMethodField()
    pending_validations = serializers.SerializerMethodField()
    has_pending_validation = serializers.SerializerMethodField()
    is_partial = serializers.BooleanField(read_only=True)
    nodeStates = serializers.SerializerMethodField()  # V2 compatible - O(1) node access

    class Meta:
        model = WorkflowRun
        fields = [
            'id', 'workflow', 'user', 'started_at', 'ended_at', 'status', 'steps',
            'workflow_title', 'workflow_description', 'pending_validations', 'has_pending_validation',
            'is_partial', 'nodeStates'
        ]
        read_only_fields = [
            'id', 'started_at', 'ended_at', 'status', 'steps',
            'workflow_title', 'workflow_description', 'pending_validations', 'has_pending_validation',
            'is_partial', 'nodeStates'
        ]

    def get_workflow_title(self, obj):
        return obj.workflow.title if obj.workflow else None

    def get_workflow_description(self, obj):
        return obj.workflow.description if obj.workflow else None

    def get_nodeStates(self, obj):
        """
        Build graph-based execution state map for V2 API compatibility.
        Provides O(1) node access for frontend components.
        """
        builder = NodeExecutionStateBuilder()
        return builder.build_state(obj)
    
    def get_has_pending_validation(self, obj):
        """Check if this workflow run has any steps waiting for human validation."""
        return obj.steps.filter(status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT).exists()
    
    def get_pending_validations(self, obj):
        """Get all pending validations with route information and AI analysis."""
        pending_steps = obj.steps.filter(
            status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
        ).select_related('step_node')

        workflow = obj.workflow
        edges_by_target = {e.target: e for e in workflow.edges.all()}
        nodes_by_id = {n.node_id: n for n in workflow.nodes.all()}

        validations = []

        for step in pending_steps:
            step_data = step.step_node.data_object if step.step_node else None

            metadata = step.metadata or {}

            # Handle ConditionalNodeData
            if step_data and isinstance(step_data, ConditionalNodeData):
                available_routes = step_data.get_routes()

                ai_recommendation = metadata.get(MetadataKey.AI_RECOMMENDATION)
                ai_analysis = metadata.get(MetadataKey.ANALYSIS)

                prompt_content = step_data.prompt.content if step_data.prompt else "Evaluate the input and choose the appropriate route."

                validations.append({
                    'node_id': step.step_node.node_id,
                    'step_number': step_data.step_number,
                    'custom_prompt': prompt_content,
                    'available_routes': available_routes,
                    'current_response': step.response,
                    'step_id': step.id,
                    'ai_recommendation': ai_recommendation,
                    'ai_analysis': ai_analysis
                })

            # Handle StructuredOutputNodeData (independent routing node)
            elif step_data and isinstance(step_data, StructuredOutputNodeData):
                available_routes = step_data.get_routes()

                # Use 'explanation' instead of 'analysis' for structured output nodes
                ai_recommendation = metadata.get('ai_recommendation')
                ai_analysis = metadata.get('explanation') or metadata.get(MetadataKey.ANALYSIS)

                prompt_content = step_data.prompt.content if step_data.prompt else "Evaluate the input and choose the appropriate route."

                validations.append({
                    'node_id': step.step_node.node_id,
                    'step_number': step_data.step_number,
                    'custom_prompt': prompt_content,
                    'available_routes': available_routes,
                    'current_response': step.response,
                    'step_id': step.id,
                    'ai_recommendation': ai_recommendation,
                    'ai_analysis': ai_analysis
                })

        return validations

# StepSerializer removed - using graph-driven architecture only


class WorkflowSerializer(serializers.ModelSerializer):
    """Clean graph-driven workflow serializer - no legacy support."""
    user = serializers.ReadOnlyField(source='user.email')
    nodes = serializers.SerializerMethodField()
    edges = serializers.SerializerMethodField()
    latest_run = serializers.SerializerMethodField()

    # Dynamic properties from StartNodeData
    title = serializers.ReadOnlyField()
    description = serializers.ReadOnlyField()
    mode = serializers.ReadOnlyField()
    viewport = serializers.ReadOnlyField()

    class Meta:
        model = Workflow
        fields = [
            'id', 'user', 'version', 'parent', 'created_at',
            'viewport_x', 'viewport_y', 'viewport_zoom',
            'manual_mode_enabled', 'display_order',
            'nodes', 'edges', 'latest_run',
            'title', 'description', 'mode', 'viewport'
        ]
        read_only_fields = ['id', 'created_at', 'user', 'nodes', 'edges', 'title', 'description', 'mode', 'viewport']

    def get_latest_run(self, obj):
        """Get the latest workflow run with nodeStates for O(1) node access."""
        latest_run = WorkflowRun.active_objects.filter(workflow=obj).order_by('-created_at').first()
        if latest_run:
            return WorkflowRunSerializer(latest_run).data
        return None

    def get_nodes(self, obj):
        # Will be properly implemented after WorkflowNodeSerializer is defined
        return []

    def get_edges(self, obj):
        # Will be properly implemented after WorkflowEdgeSerializer is defined
        return []

    def create(self, validated_data):
        """Create workflow using graph-driven architecture only."""
        return Workflow.active_objects.create(**validated_data)

    def update(self, instance, validated_data):
        """Update workflow fields only - nodes/edges handled via separate APIs."""
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ==========================================
# NEW GRAPH-DRIVEN ARCHITECTURE SERIALIZERS
# ==========================================

class StepNodeDataSerializer(serializers.ModelSerializer):
    # Make fields explicitly optional for save operations
    prompt = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        required=False,
        allow_null=True
    )
    llm = serializers.PrimaryKeyRelatedField(
        queryset=LLM.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        model = StepNodeData
        fields = [
            'agent', 'prompt', 'content_files', 'embedding_files', 'llm', 'step_number',
            'max_tokens', 'temperature', 'max_context_snippets',
            'document_similarity_threshold', 'use_previous_step_files',
            'use_previous_step_embeddings', 'text_input',
            'enable_web_search'
        ]


class StartNodeDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = StartNodeData
        fields = ['title', 'description', 'mode']


class ChatOutputNodeDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatOutputNodeData
        fields = ['step_number', 'status', 'response', 'error']




class StructuredOutputNodeDataSerializer(serializers.ModelSerializer):
    routes = serializers.JSONField(required=False, allow_null=True)
    prompt = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        required=False,
        allow_null=True
    )
    llm = serializers.PrimaryKeyRelatedField(
        queryset=LLM.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        model = StructuredOutputNodeData
        fields = ['prompt', 'llm', 'routes', 'require_human_validation', 'step_number', 'text_input']

    def to_representation(self, instance):
        """Include computed routes via get_routes() method."""
        data = super().to_representation(instance)
        # Always include the computed routes
        data['routes'] = instance.get_routes()
        return data


class ConditionalNodeDataSerializer(serializers.ModelSerializer):
    routes = serializers.JSONField(required=False, allow_null=True)
    prompt = serializers.PrimaryKeyRelatedField(
        queryset=Prompt.active_objects.all(),
        required=False,
        allow_null=True
    )
    llm = serializers.PrimaryKeyRelatedField(
        queryset=LLM.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        model = ConditionalNodeData
        fields = [
            'prompt', 'llm', 'routes', 'require_human_validation', 'step_number'
        ]

    def to_representation(self, instance):
        """Include computed routes via get_routes() method."""
        data = super().to_representation(instance)
        # Always include the computed routes
        data['routes'] = instance.get_routes()
        return data


class WorkflowEdgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowEdge
        fields = [
            'workflow',
            'edge_id', 'edge_type', 'source', 'target', 'source_handle', 'target_handle',
            'data', 'selected', 'animated', 'hidden', 'deletable', 'selectable',
            'z_index', 'label', 'style', 'class_name', 'marker_start', 'marker_end',
            'path_options'
        ]

    def validate(self, data):
        """
        Validate edge connections for start node chaining.

        Logs warnings for:
        - Chat Output → Start Node connections (chaining)
        - Start Node → Start Node connections (unusual but allowed)
        """
        # Get workflow to access nodes
        workflow = data.get('workflow')
        if not workflow:
            return data

        source_id = data.get('source')
        target_id = data.get('target')

        if not source_id or not target_id:
            return data

        # Get source and target nodes
        try:
            source_node = workflow.nodes.get(node_id=source_id)
            target_node = workflow.nodes.get(node_id=target_id)
        except WorkflowNode.DoesNotExist:
            # Nodes don't exist yet, skip validation
            return data

        # Check for Chat Output → Start Node (chaining)
        if source_node.node_type == 'chatOutput' and target_node.node_type == 'start':
            logger.info(
                f"Workflow chain connection detected: {source_id} (chatOutput) → "
                f"{target_id} (start). This will enable start node chaining."
            )

        # Check for Start → Start (unusual but allowed)
        if source_node.node_type == 'start' and target_node.node_type == 'start':
            logger.warning(
                f"Start node to start node connection: {source_id} → {target_id}. "
                f"Ensure this is intentional for workflow chaining."
            )

        return data

    def to_internal_value(self, data):
        # Accept both snake_case and React Flow camelCase
        mapped = dict(data)
        # IDs/types
        if 'id' in mapped and 'edge_id' not in mapped:
            mapped['edge_id'] = mapped.get('id')
        if 'type' in mapped and 'edge_type' not in mapped:
            mapped['edge_type'] = mapped.get('type')
        # CamelCase to snake_case
        cc = {
            'sourceHandle': 'source_handle',
            'targetHandle': 'target_handle',
            'zIndex': 'z_index',
            'className': 'class_name',
            'markerStart': 'marker_start',
            'markerEnd': 'marker_end',
            'pathOptions': 'path_options',
        }
        for ck, sk in cc.items():
            if ck in mapped and sk not in mapped:
                mapped[sk] = mapped.pop(ck)
        # Coerce class_name None -> '' because model CharField doesn't allow null
        if mapped.get('class_name', None) is None:
            mapped['class_name'] = ''
        # Coerce label None -> '' because TextField doesn't allow null by default
        if mapped.get('label', None) is None:
            mapped['label'] = ''
        # Coerce JSON-like fields None -> {}
        for jf in ['marker_start', 'marker_end', 'path_options', 'style', 'data']:
            if jf in mapped and mapped[jf] is None:
                mapped[jf] = {}
        return super().to_internal_value(mapped)

    def to_representation(self, instance):
        """Convert to React Flow Edge format."""
        return {
            'id': instance.edge_id,
            'type': instance.edge_type,
            'source': instance.source,
            'target': instance.target,
            'sourceHandle': instance.source_handle or None,
            'targetHandle': instance.target_handle or None,
            'data': instance.data,
            'selected': instance.selected,
            'animated': instance.animated,
            'hidden': instance.hidden,
            'deletable': instance.deletable,
            'selectable': instance.selectable,
            'zIndex': instance.z_index,
            'label': instance.label or None,
            'style': instance.style,
            'className': instance.class_name or None,
            'markerStart': instance.marker_start if instance.marker_start else None,
            'markerEnd': instance.marker_end if instance.marker_end else None,
            'pathOptions': instance.path_options if instance.path_options else None,
        }


class WorkflowNodeSerializer(serializers.ModelSerializer):
    # Accept node data for creation; representation uses instance.data property
    data = serializers.JSONField(write_only=True, required=False)

    class Meta:
        model = WorkflowNode
        fields = [
            'workflow',
            'node_id', 'node_type', 'position_x', 'position_y', 'width', 'height',
            'selected', 'dragging', 'draggable', 'selectable', 'connectable',
            'deletable', 'hidden', 'source_position', 'target_position', 'parent_id',
            'z_index', 'drag_handle', 'style', 'class_name', 'data'
        ]

    def to_internal_value(self, data):
        # Accept both snake_case and React Flow camelCase for node fields
        mapped = dict(data)

        if 'id' in mapped and 'node_id' not in mapped:
            mapped['node_id'] = mapped.get('id')
        if 'type' in mapped and 'node_type' not in mapped:
            mapped['node_type'] = mapped.get('type')

        # Position
        if 'position' in mapped:
            pos = mapped.get('position') or {}
            mapped.setdefault('position_x', pos.get('x'))
            mapped.setdefault('position_y', pos.get('y'))

        # CamelCase to snake_case
        cc = {
            'sourcePosition': 'source_position',
            'targetPosition': 'target_position',
            'parentId': 'parent_id',
            'zIndex': 'z_index',
            'dragHandle': 'drag_handle',
            'className': 'class_name',
        }
        for ck, sk in cc.items():
            if ck in mapped and sk not in mapped:
                mapped[sk] = mapped.pop(ck)
        # Coerce nullable-like CharFields to empty strings
        for k in ['source_position', 'target_position', 'drag_handle', 'class_name']:
            if mapped.get(k, None) is None:
                mapped[k] = ''

        return super().to_internal_value(mapped)

    def to_representation(self, instance):
        """Convert to React Flow Node format."""
        return {
            'id': instance.node_id,
            'type': instance.node_type,
            'position': {'x': instance.position_x, 'y': instance.position_y},
            'data': instance.data,  # Calls the @property method
            'selected': instance.selected,
            'dragging': instance.dragging,
            'draggable': instance.draggable,
            'selectable': instance.selectable,
            'connectable': instance.connectable,
            'deletable': instance.deletable,
            'hidden': instance.hidden,
            'sourcePosition': instance.source_position or None,
            'targetPosition': instance.target_position or None,
            'parentId': instance.parent_id or None,
            'zIndex': instance.z_index,
            'dragHandle': instance.drag_handle or None,
            'width': instance.width,
            'height': instance.height,
            'style': instance.style,
            'className': instance.class_name or None,
        }

    def create(self, validated_data):
        # Handle creation with typed data based on node_type
        node_type = validated_data['node_type']
        data_dict = validated_data.pop('data', {})

        # Create appropriate data object based on type
        data_serializer_map = {
            'step': StepNodeDataSerializer,
            'start': StartNodeDataSerializer,
            'chatOutput': ChatOutputNodeDataSerializer,
            'conditional': ConditionalNodeDataSerializer,
            'structuredOutput': StructuredOutputNodeDataSerializer,
        }

        serializer_class = data_serializer_map.get(node_type)

        if serializer_class:
            # Convert camelCase keys to snake_case (frontend sends camelCase)
            snake_case_data = convert_keys_to_snake_case(data_dict or {})

            # Filter incoming data to only allowed fields for the target serializer
            allowed_fields = set(getattr(serializer_class.Meta, 'fields', []))
            filtered_data = {k: v for k, v in snake_case_data.items() if k in allowed_fields}

            data_serializer = serializer_class(data=filtered_data)
            if data_serializer.is_valid(raise_exception=True):
                data_object = data_serializer.save()

                # Set content type for generic foreign key
                validated_data['data_content_type'] = ContentType.objects.get_for_model(data_object)
                validated_data['data_object_id'] = data_object.id

        return WorkflowNode.objects.create(**validated_data)

    def update(self, instance, validated_data):
        # Update typed data if provided
        data_dict = validated_data.pop('data', None)

        if data_dict and instance.data_object:
            # Update appropriate data object based on type
            data_serializer_map = {
                'step': StepNodeDataSerializer,
                'start': StartNodeDataSerializer,
                'chatOutput': ChatOutputNodeDataSerializer,
                'conditional': ConditionalNodeDataSerializer,
                'structuredOutput': StructuredOutputNodeDataSerializer,
            }
            serializer_class = data_serializer_map.get(instance.node_type)

            if serializer_class:
                # Convert camelCase keys to snake_case (frontend sends camelCase)
                snake_case_data = convert_keys_to_snake_case(data_dict)

                # Filter incoming data to only allowed fields for the target serializer
                allowed_fields = set(serializer_class().get_fields().keys())
                filtered_data = {k: v for k, v in snake_case_data.items() if k in allowed_fields}

                # Update the existing data object
                data_serializer = serializer_class(instance.data_object, data=filtered_data, partial=True)
                if data_serializer.is_valid(raise_exception=True):
                    data_serializer.save()

        # Update simple fields on node instance
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# Patch WorkflowSerializer methods now that node/edge serializers are defined
WorkflowSerializer.get_nodes = lambda self, obj: WorkflowNodeSerializer(obj.nodes.all(), many=True).data
WorkflowSerializer.get_edges = lambda self, obj: WorkflowEdgeSerializer(obj.edges.all(), many=True).data


# ==========================================
# V2 API SERIALIZERS (GRAPH-BASED)
# ==========================================

class WorkflowRunV2Serializer(serializers.ModelSerializer):
    """
    V2 serializer for WorkflowRun with graph-based nodeStates.

    Key Differences from V1:
    - Uses `nodeStates` (dict) instead of `steps` (list)
    - Direct O(1) node access by node_id
    - All nodes in workflow included (execution + display)
    - Validation context normalized across node types
    - Consistent data shape across all endpoints

    Response Structure:
        {
            "id": int,
            "workflow": int,
            "user": int,
            "started_at": datetime,
            "ended_at": datetime | null,
            "status": str,
            "nodeStates": {
                "node-id": {
                    "stepId": int | null,
                    "nodeType": str,
                    "status": str,
                    "response": str | null,
                    "error": str | null,
                    "validationContext": dict | null
                },
                ...
            },
            "workflow_title": str,
            "workflow_description": str,
            "is_partial": bool
        }
    """

    nodeStates = serializers.SerializerMethodField()
    started_at = serializers.DateTimeField()
    status = serializers.CharField()
    workflow_title = serializers.SerializerMethodField()
    workflow_description = serializers.SerializerMethodField()
    is_partial = serializers.BooleanField(read_only=True)

    class Meta:
        model = WorkflowRun
        fields = [
            'id', 'workflow', 'user', 'started_at', 'ended_at', 'status',
            'nodeStates',  # NEW - replaces 'steps' from v1
            'workflow_title', 'workflow_description', 'is_partial'
        ]
        read_only_fields = [
            'id', 'started_at', 'ended_at', 'status', 'nodeStates',
            'workflow_title', 'workflow_description', 'is_partial'
        ]

    def get_nodeStates(self, obj):
        """
        Build graph-based execution state map using NodeExecutionStateBuilder.

        This is where the magic happens - transforms list-based WorkflowRunStep
        data into a graph-based map keyed by node_id.

        Performance:
            - 3 database queries total (with prefetching)
            - Cached at serializer level
            - O(1) access for frontend
        """
        builder = NodeExecutionStateBuilder()
        return builder.build_state(obj)

    # Reuse existing methods from v1 serializer
    get_workflow_title = WorkflowRunSerializer.get_workflow_title
    get_workflow_description = WorkflowRunSerializer.get_workflow_description
