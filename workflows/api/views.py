from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from common.permissions import IsOwner
from workflows.api.serializers import (
    WorkflowRunSerializer, WorkflowSerializer,
    WorkflowNodeSerializer, WorkflowEdgeSerializer,
)
from workflows.constants import WorkflowRunStepStatus
from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep,
    # New graph-driven models
    WorkflowNode, WorkflowEdge, StepNodeData, StartNodeData, ChatOutputNodeData, AggregatorNodeData
)
from django_rq import enqueue
from workflows.tasks import execute_workflow_run
import weasyprint
import tempfile
import os
import markdown


class WorkflowViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting workflows."""
    serializer_class = WorkflowSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Workflow.active_objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        with transaction.atomic():
            # First update workflow scalar fields (like viewport)
            partial = kwargs.pop('partial', False)
            base_serializer = self.get_serializer(instance, data=request.data, partial=partial)
            base_serializer.is_valid(raise_exception=True)
            self.perform_update(base_serializer)

            # Upsert nodes if provided
            nodes = request.data.get('nodes', None)
            if nodes is not None:
                existing_nodes = {n.node_id: n for n in instance.nodes.all()}
                seen_ids = set()
                for n in nodes:
                    node_id = n.get('node_id') or n.get('id')
                    if not node_id:
                        continue
                    seen_ids.add(node_id)
                    existing = existing_nodes.get(node_id)
                    payload = {**n, 'workflow': instance.id}
                    if existing:
                        ser = WorkflowNodeSerializer(existing, data=payload, partial=True)
                        ser.is_valid(raise_exception=True)
                        ser.save()
                    else:
                        ser = WorkflowNodeSerializer(data=payload)
                        ser.is_valid(raise_exception=True)
                        ser.save()
                # Delete nodes that are not in payload
                for n in instance.nodes.exclude(node_id__in=seen_ids):
                    n.delete()

            # Upsert edges if provided
            edges = request.data.get('edges', None)
            if edges is not None:
                existing_edges = {e.edge_id: e for e in instance.edges.all()}
                seen_eids = set()
                for e in edges:
                    edge_id = e.get('edge_id') or e.get('id')
                    if not edge_id:
                        continue
                    seen_eids.add(edge_id)
                    existing_e = existing_edges.get(edge_id)
                    payload = {**e, 'workflow': instance.id}
                    if existing_e:
                        ser = WorkflowEdgeSerializer(existing_e, data=payload, partial=True)
                        ser.is_valid(raise_exception=True)
                        ser.save()
                    else:
                        ser = WorkflowEdgeSerializer(data=payload)
                        ser.is_valid(raise_exception=True)
                        ser.save()
                # Delete edges not in payload
                for e in instance.edges.exclude(edge_id__in=seen_eids):
                    e.delete()

            # Return full workflow with nodes/edges
            output = self.get_serializer(instance).data
            return Response(output, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        """
        Create a workflow and, if provided, persist nodes and edges from the same payload.
        Supports both snake_case and React Flow-style camelCase keys.
        """
        with transaction.atomic():
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            workflow = serializer.instance

            # Persist nodes if provided
            nodes = request.data.get('nodes') or []
            for n in nodes:
                node_ser = WorkflowNodeSerializer(data={**n, 'workflow': workflow.id})
                node_ser.is_valid(raise_exception=True)
                node_ser.save()

            # Persist edges if provided
            edges = request.data.get('edges') or []
            for e in edges:
                edge_ser = WorkflowEdgeSerializer(data={**e, 'workflow': workflow.id})
                edge_ser.is_valid(raise_exception=True)
                edge_ser.save()

            # Return full workflow with nodes and edges
            output = self.get_serializer(workflow).data
            headers = self.get_success_headers(output)
            return Response(output, status=status.HTTP_201_CREATED, headers=headers)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'], url_path='clone')
    def clone_workflow(self, request, pk=None):
        """Custom action to clone a workflow using graph-driven architecture."""
        instance = self.get_object()

        # Create cloned workflow
        cloned_workflow = Workflow.objects.create(
            user=instance.user,
            version=1,
            parent=instance,
            viewport_x=instance.viewport_x,
            viewport_y=instance.viewport_y,
            viewport_zoom=instance.viewport_zoom
        )

        # Clone all nodes and their data
        for node in instance.nodes.all():
            # Clone the typed data object first
            if node.data_object:
                cloned_data = None
                if isinstance(node.data_object, StartNodeData):
                    cloned_data = StartNodeData.objects.create(
                        title=f"COPY OF - {node.data_object.title}",
                        description=node.data_object.description,
                        mode=node.data_object.mode
                    )
                elif isinstance(node.data_object, StepNodeData):
                    cloned_data = StepNodeData.objects.create(
                        prompt=node.data_object.prompt,
                        llm=node.data_object.llm,
                        step_number=node.data_object.step_number,
                        max_tokens=node.data_object.max_tokens,
                        temperature=node.data_object.temperature,
                        max_context_snippets=node.data_object.max_context_snippets,
                        document_similarity_threshold=node.data_object.document_similarity_threshold,
                        use_previous_step_files=node.data_object.use_previous_step_files,
                        use_previous_step_embeddings=node.data_object.use_previous_step_embeddings
                    )
                    # Clone many-to-many relationships
                    cloned_data.content_files.set(node.data_object.content_files.all())
                    cloned_data.embedding_files.set(node.data_object.embedding_files.all())
                elif isinstance(node.data_object, ChatOutputNodeData):
                    cloned_data = ChatOutputNodeData.objects.create(
                        step_number=node.data_object.step_number,
                        status='',
                        response='',
                        error=''
                    )
                elif isinstance(node.data_object, AggregatorNodeData):
                    cloned_data = AggregatorNodeData.objects.create(
                        scoring_mode=node.data_object.scoring_mode,
                        custom_prompt=node.data_object.custom_prompt,
                        step_number=node.data_object.step_number
                    )

                if cloned_data:
                    # Create cloned node
                    WorkflowNode.objects.create(
                        workflow=cloned_workflow,
                        node_id=node.node_id,
                        node_type=node.node_type,
                        position_x=node.position_x,
                        position_y=node.position_y,
                        width=node.width,
                        height=node.height,
                        selected=False,
                        dragging=False,
                        draggable=node.draggable,
                        selectable=node.selectable,
                        connectable=node.connectable,
                        deletable=node.deletable,
                        hidden=node.hidden,
                        source_position=node.source_position,
                        target_position=node.target_position,
                        parent_id=node.parent_id,
                        z_index=node.z_index,
                        drag_handle=node.drag_handle,
                        style=node.style,
                        class_name=node.class_name,
                        data_content_type=ContentType.objects.get_for_model(cloned_data),
                        data_object_id=cloned_data.id
                    )

        # Clone all edges
        for edge in instance.edges.all():
            WorkflowEdge.objects.create(
                workflow=cloned_workflow,
                edge_id=edge.edge_id,
                edge_type=edge.edge_type,
                source=edge.source,
                target=edge.target,
                source_handle=edge.source_handle,
                target_handle=edge.target_handle,
                data=edge.data,
                selected=False,
                animated=edge.animated,
                hidden=edge.hidden,
                deletable=edge.deletable,
                selectable=edge.selectable,
                z_index=edge.z_index,
                label=edge.label,
                style=edge.style,
                class_name=edge.class_name,
                marker_start=edge.marker_start,
                marker_end=edge.marker_end,
                path_options=edge.path_options
            )

        serializer = self.get_serializer(cloned_workflow)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

# StepViewSet removed - steps now managed via WorkflowNode with StepNodeData

class WorkflowRunViewSet(viewsets.ModelViewSet):
    serializer_class = WorkflowRunSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return WorkflowRun.active_objects.filter(user=self.request.user).order_by('-created_at')

    @action(detail=False, methods=['post'], url_path='run-workflow')
    def run_workflow(self, request):
        workflow_id = request.data.get('workflow_id')
        if not workflow_id:
            return Response({"error": "workflow_id is required"}, status=400)
        try:
            workflow = Workflow.active_objects.get(id=workflow_id, user=request.user)
        except Workflow.DoesNotExist:
            return Response({"error": "Workflow not found"}, status=404)

        # Check if workflow has step nodes
        step_nodes = workflow.nodes.filter(node_type='step')
        if not step_nodes.exists():
            return Response(
                {"error": "Cannot run workflow with zero step nodes. Please add at least one step node to the workflow."},
                status=400
            )

        workflow_run = WorkflowRun.objects.create(workflow=workflow, user=request.user)

        # Create WorkflowRunStep objects for each step node
        # Note: Using new node handler system, so order will be determined at execution time
        for step_node in step_nodes:
            if step_node.data_object and isinstance(step_node.data_object, StepNodeData):
                WorkflowRunStep.objects.create(
                    workflow_run=workflow_run,
                    step_node=step_node,
                    order=step_node.data_object.step_number,
                    status=WorkflowRunStepStatus.PENDING
                )

        enqueue(execute_workflow_run, workflow_run.id)

        workflow_run.refresh_from_db()

        serializer = self.get_serializer(workflow_run)
        return Response(serializer.data, status=201)

    @action(detail=True, methods=['get'], url_path='export-pdf')
    def export_pdf(self, request, pk=None):
        """Export workflow run results as a PDF."""
        
        try:
            workflow_run = self.get_object()
            
            # Get and process steps for markdown content
            steps = workflow_run.steps.all().order_by('order')
            processed_steps = []
            
            for step in steps:
                processed_step = step
                # Convert markdown to HTML for prompts and responses
                if step.step.prompt and step.step.prompt.content:
                    step.step.prompt.content = markdown.markdown(
                        step.step.prompt.content,
                        extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables', 'markdown.extensions.nl2br']
                    )
                if step.response:
                    step.response = markdown.markdown(
                        step.response,
                        extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables', 'markdown.extensions.nl2br']
                    )
                processed_steps.append(processed_step)
            
            # Prepare context for template
            context = {
                'workflow_run': workflow_run,
                'workflow': workflow_run.workflow,
                'steps': processed_steps,
                'generated_at': timezone.now(),
                'user': workflow_run.user,
            }
            
            # Render HTML template
            html_content = render_to_string('workflows/pdf_export.html', context)
            
            # Generate PDF
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                weasyprint.HTML(string=html_content).write_pdf(tmp_file.name)
                
                # Read PDF content
                with open(tmp_file.name, 'rb') as pdf_file:
                    pdf_content = pdf_file.read()
                
                # Clean up temporary file
                os.unlink(tmp_file.name)
            
            # Prepare response
            filename = f"{workflow_run.workflow.title.replace(' ', '_')}-results.pdf"
            response = HttpResponse(pdf_content, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(pdf_content)
            
            return response

        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response(
                {'error': f'Failed to generate PDF: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ==========================================
# NEW GRAPH-DRIVEN ARCHITECTURE VIEWS
# ==========================================

# NewWorkflowViewSet removed - WorkflowViewSet now handles both legacy and graph-driven workflows


class WorkflowNodeViewSet(viewsets.ModelViewSet):
    """Manage React Flow nodes in workflows."""
    serializer_class = WorkflowNodeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return WorkflowNode.objects.filter(workflow__user=self.request.user)

    # Default create/update are sufficient; ownership enforced below

    def perform_create(self, serializer):
        workflow = serializer.validated_data.get('workflow')
        if not workflow or workflow.user != self.request.user:
            raise PermissionDenied("Invalid workflow or not owned by user")
        serializer.save()

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.workflow.user != self.request.user:
            raise PermissionDenied("Not allowed to modify this node")
        # prevent changing workflow ownership via update
        serializer.validated_data.pop('workflow', None)
        serializer.save()


class WorkflowEdgeViewSet(viewsets.ModelViewSet):
    """Manage React Flow edges in workflows."""
    serializer_class = WorkflowEdgeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return WorkflowEdge.objects.filter(workflow__user=self.request.user)

    # Default create/update are sufficient; ownership enforced below

    def perform_create(self, serializer):
        workflow = serializer.validated_data.get('workflow')
        if not workflow or workflow.user != self.request.user:
            raise PermissionDenied("Invalid workflow or not owned by user")
        serializer.save()

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.workflow.user != self.request.user:
            raise PermissionDenied("Not allowed to modify this edge")
        # prevent changing workflow via update
        serializer.validated_data.pop('workflow', None)
        serializer.save()
