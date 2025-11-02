from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from common.permissions import IsOwner
import traceback
from workflows.api.serializers import (
    WorkflowRunSerializer, WorkflowSerializer,
    WorkflowNodeSerializer, WorkflowEdgeSerializer,
)
from workflows.constants import WorkflowRunStepStatus
from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep,
    # New graph-driven models
    WorkflowNode, WorkflowEdge, StepNodeData, StartNodeData, ChatOutputNodeData
)
from workflows.services import WorkflowCloningService
from django_rq import enqueue
from workflows.tasks import execute_workflow_run, resume_workflow_run
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
                nodes_to_delete = instance.nodes.exclude(node_id__in=seen_ids)
                if nodes_to_delete.exists():
                    for n in nodes_to_delete:
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
                edges_to_delete = instance.edges.exclude(edge_id__in=seen_eids)
                if edges_to_delete.exists():
                    for e in edges_to_delete:
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

        # Use the dedicated cloning service
        cloning_service = WorkflowCloningService()
        cloned_workflow = cloning_service.clone_workflow(instance)

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
        step_nodes = workflow.nodes.filter(node_type='step').select_related('data_content_type')

        # Prefetch StepNodeData for step nodes to avoid N+1 queries
        step_node_ids = list(step_nodes.values_list('data_object_id', flat=True))
        step_data_objects = {
            obj.id: obj for obj in StepNodeData.objects.filter(
                id__in=step_node_ids
            ).select_related('prompt', 'llm').prefetch_related('content_files', 'embedding_files')
        }
        if not step_nodes.exists():
            return Response(
                {"error": "Cannot run workflow with zero step nodes. Please add at least one step node to the workflow."},
                status=400
            )

        workflow_run = WorkflowRun.objects.create(workflow=workflow, user=request.user)

        # Create WorkflowRunStep objects for each step node
        # Note: Using new node handler system, so order will be determined at execution time
        for step_node in step_nodes:
            step_data = step_data_objects.get(step_node.data_object_id)
            if step_data and isinstance(step_data, StepNodeData):
                WorkflowRunStep.objects.create(
                    workflow_run=workflow_run,
                    step_node=step_node,
                    order=step_data.step_number,
                    status=WorkflowRunStepStatus.PENDING
                )

        enqueue(execute_workflow_run, workflow_run.id)

        workflow_run.refresh_from_db()

        serializer = self.get_serializer(workflow_run)
        return Response(serializer.data, status=201)

    @action(detail=True, methods=['post'], url_path='submit-human-validation')
    def submit_human_validation(self, request, pk=None):
        """
        Submit user's route choice for a node requiring human validation.
        Handles both ConditionalNode and StructuredOutputNode.

        Expected request data (frontend sends camelCase, DRF parser converts to snake_case):
        {
            "node_id": "conditional_node_123" or "structured-output-1",
            "chosen_route": "Route A"
        }
        """
        workflow_run = self.get_object()
        # CamelCaseJSONParser automatically converts incoming camelCase to snake_case
        node_id = request.data.get('node_id')
        chosen_route = request.data.get('chosen_route')

        if not node_id:
            return Response({'error': 'node_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not chosen_route:
            return Response({'error': 'chosen_route is required'}, status=status.HTTP_400_BAD_REQUEST)

        # Find the step waiting for input
        # For ConditionalNode: step_node__node_id matches node_id directly
        # For StructuredOutputNode: need to check metadata
        pending_step = None

        # First try direct match (ConditionalNode case)
        try:
            pending_step = WorkflowRunStep.objects.get(
                workflow_run=workflow_run,
                step_node__node_id=node_id,
                status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
            )
        except WorkflowRunStep.DoesNotExist:
            # Try StructuredOutputNode case - find by metadata flag (MUST USE SNAKE_CASE)
            pending_steps = WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
            )

            for step in pending_steps:
                metadata = step.metadata or {}

                # MUST USE SNAKE_CASE - backend stores as use_structured_output_node
                if metadata.get('use_structured_output_node'):
                    # Check if the structured output node ID matches
                    from workflows.models import WorkflowEdge
                    incoming_edges = WorkflowEdge.objects.filter(
                        workflow=workflow_run.workflow,
                        target=step.step_node.node_id
                    )

                    for edge in incoming_edges:
                        if edge.source == node_id:
                            pending_step = step
                            break

                    if pending_step:
                        break

        if not pending_step:
            return Response(
                {'error': f'No pending validation found for node {node_id}'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validate that the chosen route is valid
        from workflows.models import ConditionalNodeData, StructuredOutputNodeData, StepNodeData
        step_data = pending_step.step_node.data_object

        # Determine which type of validation this is
        if isinstance(step_data, ConditionalNodeData):
            available_routes = step_data.get_routes()
        elif isinstance(step_data, StepNodeData) and pending_step.metadata.get('use_structured_output_node'):
            # Find the StructuredOutputNode
            from workflows.models import WorkflowEdge
            incoming_edges = WorkflowEdge.objects.filter(
                workflow=workflow_run.workflow,
                target=pending_step.step_node.node_id
            )

            structured_node = None
            for edge in incoming_edges:
                node = workflow_run.workflow.nodes.filter(
                    node_id=edge.source,
                    node_type='structuredOutput'
                ).first()
                if node and node.data_object:
                    structured_node = node.data_object
                    break

            if structured_node and isinstance(structured_node, StructuredOutputNodeData):
                available_routes = structured_node.get_routes()
            else:
                return Response(
                    {'error': 'Could not find StructuredOutputNode'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            return Response(
                {'error': 'Invalid node type - expected conditional or structured output node'},
                status=status.HTTP_400_BAD_REQUEST
            )

        route_names = [r['name'] for r in available_routes]

        if chosen_route not in route_names:
            return Response(
                {
                    'error': f'Invalid route choice. Available routes: {", ".join(route_names)}',
                    'available_routes': available_routes
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Enqueue the resume task - use step node's ID for routing
        resume_node_id = pending_step.step_node.node_id
        enqueue(resume_workflow_run, workflow_run.id, resume_node_id, chosen_route)

        return Response({
            'message': 'Route chosen successfully. Workflow will resume execution.',
            'chosen_route': chosen_route,
            'node_id': node_id,
            'workflow_run_id': workflow_run.id
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='pending-validations')
    def get_pending_validations(self, request, pk=None):
        """
        Get all conditional nodes in this workflow run that are waiting for human validation.
        
        Returns a list of pending validations with route options.
        """
        workflow_run = self.get_object()
        
        # Find all steps waiting for human input
        pending_steps = WorkflowRunStep.objects.filter(
            workflow_run=workflow_run,
            status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
        ).select_related('step_node')
        
        validations = []
        from workflows.models import ConditionalNodeData
        
        for step in pending_steps:
            conditional_data = step.step_node.data_object
            
            if isinstance(conditional_data, ConditionalNodeData):
                available_routes = conditional_data.get_routes()
                # Get prompt content if prompt exists
                prompt_content = conditional_data.prompt.content if conditional_data.prompt else "Evaluate the input and choose the appropriate route."

                validations.append({
                    'node_id': step.step_node.node_id,
                    'step_number': conditional_data.step_number,
                    'custom_prompt': prompt_content,  # For backward compatibility with frontend
                    'available_routes': available_routes,
                    'current_response': step.response,
                    'step_id': step.id
                })
        
        return Response({
            'workflow_run_id': workflow_run.id,
            'pending_validations': validations,
            'count': len(validations)
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='export-pdf')
    def export_pdf(self, request, pk=None):
        """Export workflow run results as a PDF."""

        try:
            workflow_run = self.get_object()

            # Get and process steps for markdown content
            steps = workflow_run.steps.all().order_by('order').select_related('step_node')
            processed_steps = []

            for step in steps:
                # Get step data from the new node-based structure
                step_data = step.step_data  # Uses @property that gets data from step_node

                # Convert markdown to HTML for prompts and responses
                if step_data and step_data.prompt and step_data.prompt.content:
                    # Create attributes on the step object for template access
                    step.prompt_content_html = markdown.markdown(
                        step_data.prompt.content,
                        extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables', 'markdown.extensions.nl2br']
                    )
                    step.prompt_title = step_data.prompt.title if hasattr(step_data.prompt, 'title') else 'Untitled Prompt'
                else:
                    step.prompt_content_html = ''
                    step.prompt_title = 'No Prompt'

                if step.response:
                    step.response_html = markdown.markdown(
                        step.response,
                        extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables', 'markdown.extensions.nl2br']
                    )
                else:
                    step.response_html = ''

                processed_steps.append(step)
            
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
            traceback.print_exc()
            return Response(
                {'error': f'Failed to generate PDF: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ==========================================
# NEW GRAPH-DRIVEN ARCHITECTURE VIEWS
# ==========================================

# NewWorkflowViewSet removed - WorkflowViewSet now handles both legacy and graph-driven workflows
# WorkflowNodeViewSet and WorkflowEdgeViewSet removed - nodes/edges are managed via nested data in WorkflowViewSet
