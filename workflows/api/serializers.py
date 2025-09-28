from rest_framework import serializers
from django.contrib.contenttypes.models import ContentType
from files.api.serializers import FileSerializer
from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep,  # WorkflowStepSnippet,
    # Graph-driven models
    StepNodeData, StartNodeData, ChatOutputNodeData, ConditionalNodeData,
    WorkflowNode, WorkflowEdge
)
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers import NodeDataHandler


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
        fields = ['id', 'step_node', 'order', 'status', 'response', 'error', 'created_at', 'updated_at']  # removed 'snippets'
        read_only_fields = ['id', 'created_at', 'updated_at']  # removed 'snippets'

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
            'nodes', 'edges', 'latest_run',
            'title', 'description', 'mode', 'viewport'
        ]
        read_only_fields = ['id', 'created_at', 'user', 'nodes', 'edges', 'title', 'description', 'mode', 'viewport']

    def get_latest_run(self, obj):
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
    class Meta:
        model = StepNodeData
        fields = [
            'prompt', 'content_files', 'embedding_files', 'llm', 'step_number',
            'max_tokens', 'temperature', 'max_context_snippets',
            'document_similarity_threshold', 'use_previous_step_files',
            'use_previous_step_embeddings'
        ]


class StartNodeDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = StartNodeData
        fields = ['title', 'description', 'mode']


class ChatOutputNodeDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatOutputNodeData
        fields = ['step_number', 'status', 'response', 'error']




class ConditionalNodeDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConditionalNodeData
        fields = [
            'custom_prompt', 'route_a_name', 'route_b_name',
            'route_a_description', 'route_b_description', 'step_number'
        ]


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
        # Normalize node data keys based on node_type
        node_type = mapped.get('node_type')
        node_data = NodeDataHandler.normalize_data(node_type, mapped)
        mapped['data'] = node_data
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
        }

        serializer_class = data_serializer_map.get(node_type)
        if serializer_class:
            # Filter incoming data to only allowed fields for the target serializer
            allowed_fields = set(getattr(serializer_class.Meta, 'fields', []))
            filtered_data = {k: v for k, v in (data_dict or {}).items() if k in allowed_fields}
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
            }
            serializer_class = data_serializer_map.get(instance.node_type)
            if serializer_class:
                # Filter incoming data to only allowed fields for the target serializer
                allowed_fields = set(serializer_class().get_fields().keys())
                filtered_data = {k: v for k, v in data_dict.items() if k in allowed_fields}
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
