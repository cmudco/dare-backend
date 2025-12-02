import logging
import markdown
import os
import tempfile
import traceback

import weasyprint
from asgiref.sync import async_to_sync
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django_rq import enqueue
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from common.permissions import IsOwner
from core.services.workflow_execution_service import WorkflowExecutionService
from workflows.api.serializers import (
    WorkflowRunSerializer, WorkflowSerializer,
    WorkflowNodeSerializer, WorkflowEdgeSerializer,
    WorkflowRunV2Serializer,
)
from workflows.constants import WorkflowRunStepStatus
from workflows.handlers.utils import MetadataKey, ExecutionValidator
from workflows.handlers.utils.workflow_validator import WorkflowValidator
from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep,
    WorkflowNode, WorkflowEdge, StepNodeData, StartNodeData, ChatOutputNodeData,
    StructuredOutputNodeData
)
from workflows.services import WorkflowCloningService
from workflows.tasks import execute_workflow_run, resume_workflow_run


logger = logging.getLogger(__name__)


class WorkflowViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting workflows."""
    serializer_class = WorkflowSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Workflow.active_objects.filter(
            user=self.request.user
        ).prefetch_related(
            Prefetch(
                'nodes',
                queryset=WorkflowNode.objects.filter(node_type='start').select_related('data_content_type'),
                to_attr='_cached_start_nodes'
            ),
            'nodes',
            'edges'
        ).order_by('display_order', '-created_at')

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

    @action(detail=True, methods=['patch'], url_path='toggle-manual-mode')
    def toggle_manual_mode(self, request, pk=None):
        """
        Toggle manual mode (step-by-step execution) for a workflow.

        Body:
            manual_mode_enabled: bool (required)

        Response:
            Updated Workflow object with manual_mode_enabled field
        """
        workflow = self.get_object()
        manual_mode_enabled = request.data.get('manual_mode_enabled')

        if manual_mode_enabled is None:
            return Response({"error": "manual_mode_enabled is required"}, status=400)

        workflow.manual_mode_enabled = manual_mode_enabled
        workflow.save(update_fields=['manual_mode_enabled'])

        serializer = self.get_serializer(workflow)
        return Response(serializer.data, status=200)

    @action(detail=False, methods=['patch'], url_path='update-display-order')
    def update_display_order(self, request):
        """
        Update the display order of multiple workflows.
        Expected payload: [{"id": 1, "display_order": 10}, {"id": 2, "display_order": 20}, ...]
        """
        try:
            updates = request.data
            if not isinstance(updates, list):
                return Response(
                    {"error": "Expected a list of workflow updates"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            workflow_ids = [update.get('id') for update in updates]
            workflows = Workflow.active_objects.filter(
                user=request.user,
                id__in=workflow_ids
            )

            workflow_map = {wf.id: wf for wf in workflows}

            for update in updates:
                workflow_id = update.get('id')
                display_order = update.get('display_order')

                if workflow_id in workflow_map and display_order is not None:
                    workflow_map[workflow_id].display_order = display_order
                    workflow_map[workflow_id].save(update_fields=['display_order'])

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

# StepViewSet removed - steps now managed via WorkflowNode with StepNodeData

class WorkflowRunViewSet(viewsets.ModelViewSet):
    serializer_class = WorkflowRunSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        # Prefetch steps with their related step_node to avoid N+1 queries
        # and ensure step_node is properly populated in serialization
        steps_prefetch = Prefetch(
            'steps',
            queryset=WorkflowRunStep.objects.select_related('step_node').order_by('order')
        )
        queryset = WorkflowRun.active_objects.filter(user=self.request.user).prefetch_related(
            steps_prefetch
        ).select_related('workflow').order_by('-created_at')

        workflow_id = self.request.query_params.get('workflow')
        if workflow_id:
            queryset = queryset.filter(workflow_id=workflow_id)

        return queryset

    @action(detail=False, methods=['post'], url_path='run-workflow')
    def run_workflow(self, request):
        workflow_id = request.data.get('workflow_id')
        if not workflow_id:
            return Response({"error": "workflow_id is required"}, status=400)
        try:
            workflow = Workflow.active_objects.get(id=workflow_id, user=request.user)
        except Workflow.DoesNotExist:
            return Response({"error": "Workflow not found"}, status=404)

        # Pre-execution validation: Ensure all nodes are properly configured
        is_valid, validation_errors = WorkflowValidator.validate_for_execution(workflow)
        if not is_valid:
            return Response(
                {
                    "error": "Workflow validation failed",
                    "validation_errors": validation_errors,
                },
                status=400
            )

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

        # Check for existing partial run when workflow has manual mode enabled
        # If partial run exists, continue it; otherwise create new run
        partial_run = WorkflowRun.active_objects.filter(
            workflow=workflow,
            user=request.user,
            is_partial=True,
            ended_at__isnull=True  # Only get incomplete runs
        ).order_by('-created_at').first()

        if partial_run:
            # Continue existing partial run
            # Mark it as non-partial since we're completing it in full mode
            partial_run.is_partial = False
            partial_run.save(update_fields=['is_partial'])
            workflow_run = partial_run

            # Create WorkflowRunStep objects for steps that haven't been created yet
            existing_step_node_ids = set(
                workflow_run.steps.values_list('step_node_id', flat=True)
            )
            for step_node in step_nodes:
                if step_node.id not in existing_step_node_ids:
                    step_data = step_data_objects.get(step_node.data_object_id)
                    if step_data and isinstance(step_data, StepNodeData):
                        WorkflowRunStep.objects.create(
                            workflow_run=workflow_run,
                            step_node=step_node,
                            order=step_data.step_number,
                            status=WorkflowRunStepStatus.PENDING
                        )
        else:
            # Create new workflow run
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

    @action(detail=False, methods=['post'], url_path='execute-single-step')
    def execute_single_step(self, request):
        """
        Execute a single workflow step for manual step-by-step execution.

        Validates dependencies before execution and returns step result immediately.
        Creates or reuses a partial WorkflowRun.

        Request body:
        {
            "workflow_id": int,
            "step_node_id": str,
            "workflow_run_id": int | null  (optional, for continuing partial run)
        }

        Response:
        {
            "success": bool,
            "workflow_run_id": int,
            "step_result": {
                "step_id": int,
                "node_id": str,
                "status": str,
                "response": str,
                "error": str | null,
                "metadata": dict
            } | null,
            "missing_dependencies": [str],  (list of node IDs)
            "error": str | null
        }
        """
        workflow_id = request.data.get('workflow_id')
        step_node_id = request.data.get('step_node_id')
        workflow_run_id = request.data.get('workflow_run_id')

        logger.info(f"Execute single step request: workflow_id={workflow_id}, step_node_id={step_node_id}, workflow_run_id={workflow_run_id}")

        # Validate required fields
        if not workflow_id:
            return Response({"error": "workflow_id is required"}, status=400)
        if not step_node_id:
            return Response({"error": "step_node_id is required"}, status=400)

        # Get workflow
        try:
            workflow = Workflow.active_objects.get(id=workflow_id, user=request.user)
        except Workflow.DoesNotExist:
            return Response({"error": "Workflow not found"}, status=404)

        # Get or create partial workflow run
        if workflow_run_id:
            try:
                workflow_run = WorkflowRun.objects.get(
                    id=workflow_run_id,
                    workflow=workflow,
                    user=request.user,
                    is_partial=True
                )
            except WorkflowRun.DoesNotExist:
                return Response({"error": "Partial workflow run not found"}, status=404)
        else:
            # Create new partial run
            workflow_run = WorkflowRun.objects.create(
                workflow=workflow,
                user=request.user,
                is_partial=True
            )

        # Validate that step_node_id exists in this workflow
        try:
            step_node = WorkflowNode.objects.get(
                workflow=workflow,
                node_id=step_node_id
            )
        except WorkflowNode.DoesNotExist:
            return Response(
                {"error": f"Step node {step_node_id} not found in workflow"},
                status=404
            )

        if step_node.node_type == 'step':
            node_errors = ExecutionValidator._validate_step_node(step_node)
        elif step_node.node_type == 'structuredOutput':
            node_errors = ExecutionValidator._validate_structured_output_node(step_node)
        else:
            node_errors = []

        if node_errors:
            return Response(
                {
                    "error": "Node validation failed. Please complete required fields.",
                    "validation_errors": node_errors,
                    "node_id": step_node_id
                },
                status=400
            )

        # Get or create WorkflowRunStep for this node
        workflow_run_step, created = WorkflowRunStep.objects.get_or_create(
            workflow_run=workflow_run,
            step_node=step_node,
            defaults={
                'order': getattr(step_node.data_object, 'step_number', 0),
                'status': WorkflowRunStepStatus.PENDING
            }
        )

        # Execute single step using the service
        service = WorkflowExecutionService()

        try:
            # Run async execution in sync context using async_to_sync
            result = async_to_sync(service.execute_single_step)(
                workflow_run, step_node_id
            )

            # If execution failed due to missing dependencies
            if not result['success'] and result['missing_dependencies']:
                logger.warning(f"Step {step_node_id} blocked by missing dependencies: {result['missing_dependencies']}")
                return Response({
                    'success': False,
                    'workflow_run_id': workflow_run.id,
                    'step_result': None,
                    'missing_dependencies': result['missing_dependencies'],
                    'error': result['error']
                }, status=200)

            # Get updated step data
            workflow_run_step.refresh_from_db()

            step_result_data = {
                'step_id': workflow_run_step.id,
                'node_id': step_node_id,
                'status': workflow_run_step.status,
                'response': workflow_run_step.response,
                'error': workflow_run_step.error,
                'metadata': workflow_run_step.metadata or {}
            }

            logger.info(f"Step {step_node_id} execution completed with success={result['success']}")
            return Response({
                'success': result['success'],
                'workflow_run_id': workflow_run.id,
                'step_result': step_result_data,
                'missing_dependencies': [],
                'error': result.get('error')
            }, status=200)

        except Exception as e:
            logger.error(f"Exception during step {step_node_id} execution: {str(e)}", exc_info=True)
            return Response({
                'success': False,
                'workflow_run_id': workflow_run.id,
                'step_result': None,
                'missing_dependencies': [],
                'error': f'Execution error: {str(e)}'
            }, status=200)

    @action(detail=False, methods=['get'], url_path='get-active-partial-run')
    def get_active_partial_run(self, request):
        """
        Get the most recent active partial workflow run for a specific workflow.

        Query params:
            workflow_id: int (required)

        Response:
            WorkflowRun object with steps and executed node IDs, or null if no active partial run
        """
        workflow_id = request.query_params.get('workflow_id')

        if not workflow_id:
            return Response({"error": "workflow_id is required"}, status=400)

        try:
            workflow = Workflow.active_objects.get(id=workflow_id, user=request.user)
        except Workflow.DoesNotExist:
            return Response({"error": "Workflow not found"}, status=404)

        # Get the most recent partial run (incomplete runs only)
        # An incomplete run is one where ended_at is null OR marked as is_partial=True
        partial_run = WorkflowRun.active_objects.filter(
            workflow=workflow,
            user=request.user,
            is_partial=True,
            ended_at__isnull=True  # Only get incomplete runs
        ).order_by('-created_at').first()

        if not partial_run:
            return Response({
                'partialRun': None,
                'executedStepNodeIds': []
            }, status=200)

        # Get all completed step node IDs from this partial run
        completed_steps = partial_run.steps.filter(
            status__in=['completed', 'skipped']
        ).select_related('step_node')

        executed_node_ids = [
            step.step_node.node_id
            for step in completed_steps
            if step.step_node
        ]

        # Also include output nodes connected to executed steps
        workflow_edges = workflow.edges.all()
        for step in completed_steps:
            if not step.step_node:
                continue
            # Find edges where this step is the source
            connected_edges = [e for e in workflow_edges if e.source == step.step_node.node_id]
            for edge in connected_edges:
                # Check if target is an output node
                target_node = workflow.nodes.filter(node_id=edge.target).first()
                if target_node and target_node.node_type in ['chatOutput', 'structuredOutput']:
                    executed_node_ids.append(edge.target)

        # Serialize the partial run
        serializer = self.get_serializer(partial_run)
        partial_run_data = serializer.data

        # Enrich steps with node_id for easier frontend mapping
        enriched_steps = []
        for step_data in partial_run_data.get('steps', []):
            step = partial_run.steps.filter(id=step_data['id']).select_related('step_node').first()
            if step and step.step_node:
                step_data['node_id'] = step.step_node.node_id
            enriched_steps.append(step_data)

        partial_run_data['steps'] = enriched_steps

        return Response({
            'partialRun': partial_run_data,
            'executedStepNodeIds': list(set(executed_node_ids))  # Remove duplicates
        }, status=200)

    @action(detail=True, methods=['post'], url_path='submit-human-validation')
    def submit_human_validation(self, request, pk=None):
        """
        Submit user's route choice for a node requiring human validation.

        Handles StructuredOutputNode validations.
        Frontend sends: {"nodeId": "...", "chosenRoute": "..."}
        DRF CamelCaseJSONParser converts to: {"node_id": "...", "chosen_route": "..."}
        """
        workflow_run = self.get_object()
        node_id = request.data.get('node_id')
        chosen_route = request.data.get('chosen_route')

        # Validate required fields
        if not node_id:
            return Response(
                {'error': 'node_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not chosen_route:
            return Response(
                {'error': 'chosen_route is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Find pending validation step
        pending_step = self._find_pending_validation_step(workflow_run, node_id)
        if not pending_step:
            return Response(
                {'error': f'No pending validation found for node {node_id}'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get available routes for validation
        available_routes = self._get_available_routes(workflow_run, pending_step, node_id)
        if available_routes is None:
            return Response(
                {'error': 'Could not determine available routes for this node'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate chosen route
        route_names = [r['name'] for r in available_routes]
        if chosen_route not in route_names:
            return Response(
                {
                    'error': f'Invalid route choice. Available routes: {", ".join(route_names)}',
                    'available_routes': available_routes
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Resume workflow execution
        enqueue(resume_workflow_run, workflow_run.id, pending_step.step_node.node_id, chosen_route)

        return Response({
            'message': 'Route chosen successfully. Workflow will resume execution.',
            'chosen_route': chosen_route,
            'node_id': node_id,
            'workflow_run_id': workflow_run.id
        }, status=status.HTTP_200_OK)

    def _find_pending_validation_step(self, workflow_run, node_id):
        """
        Find the WorkflowRunStep waiting for human validation.

        Returns:
            WorkflowRunStep or None
        """
        # Try direct match
        pending_step = WorkflowRunStep.objects.filter(
            workflow_run=workflow_run,
            step_node__node_id=node_id,
            status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
        ).first()

        if pending_step:
            return pending_step

        # Try StructuredOutputNode case - find step with metadata flag
        pending_steps = WorkflowRunStep.objects.filter(
            workflow_run=workflow_run,
            status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
        ).select_related('step_node')

        for step in pending_steps:
            metadata = step.metadata or {}
            if not metadata.get(MetadataKey.USE_STRUCTURED_OUTPUT_NODE):
                continue

            # Check if this step connects to the requested structured output node
            edge_exists = WorkflowEdge.objects.filter(
                workflow=workflow_run.workflow,
                source=node_id,
                target=step.step_node.node_id
            ).exists()

            if edge_exists:
                return step

        return None

    def _get_available_routes(self, workflow_run, pending_step, node_id):
        """
        Get available routes for the pending validation.

        Returns:
            List of route dicts [{"name": "...", "description": "..."}] or None if error
        """
        step_data = pending_step.step_node.data_object

        # StructuredOutputNodeData: routing node with its own routes
        if isinstance(step_data, StructuredOutputNodeData):
            return step_data.get_routes()

        return None

    @action(detail=True, methods=['get'], url_path='pending-validations')
    def get_pending_validations(self, request, pk=None):
        """
        Get all routing nodes in this workflow run that are waiting for human validation.
        
        Returns a list of pending validations with route options.
        """
        workflow_run = self.get_object()
        
        # Find all steps waiting for human input
        pending_steps = WorkflowRunStep.objects.filter(
            workflow_run=workflow_run,
            status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
        ).select_related('step_node')

        validations = []

        for step in pending_steps:
            routing_data = step.step_node.data_object
            
            if isinstance(routing_data, StructuredOutputNodeData):
                available_routes = routing_data.get_routes()
                # Get prompt content if prompt exists
                prompt_content = routing_data.prompt.content if routing_data.prompt else "Evaluate the input and choose the appropriate route."

                validations.append({
                    'node_id': step.step_node.node_id,
                    'step_number': routing_data.step_number,
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


# ==========================================
# V2 API VIEWS (GRAPH-BASED NODE STATES)
# ==========================================

class WorkflowRunV2ViewSet(viewsets.ViewSet):
    """
    V2 API for workflow runs with graph-based nodeStates.

    Key Features:
    - Returns nodeStates (dict) instead of steps (list)
    - Unified response format across all endpoints
    - Direct O(1) node access for frontend
    - Normalized validation context

    Endpoints:
    - GET /api/v2/workflows/runs/{id}/ - Retrieve workflow run with nodeStates
    - POST /api/v2/workflows/runs/execute-single-step/ - Execute single step
    - POST /api/v2/workflows/runs/submit-human-validation/ - Submit validation choice
    """

    permission_classes = [IsAuthenticated, IsOwner]

    def retrieve(self, request, pk=None):
        """
        GET /api/v2/workflows/runs/{id}/

        Retrieve a workflow run with graph-based nodeStates.

        Returns:
            200: WorkflowRunV2Serializer data with nodeStates
            404: Workflow run not found
        """
        try:
            workflow_run = WorkflowRun.objects.get(pk=pk, user=request.user)
        except WorkflowRun.DoesNotExist:
            return Response(
                {"error": "Workflow run not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = WorkflowRunV2Serializer(workflow_run)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='execute-single-step')
    def execute_single_step(self, request):
        """
        POST /api/v2/workflows/runs/execute-single-step/

        Execute a single workflow step for manual step-by-step execution.
        V2 version returns full WorkflowRunV2Serializer for consistency.

        Request body:
        {
            "workflow_id": int,
            "step_node_id": str,
            "workflow_run_id": int | null  (optional, for continuing partial run)
        }

        Response:
        {
            "success": bool,
            "workflow_run": WorkflowRunV2Serializer data,
            "missing_dependencies": [str],
            "error": str | null
        }
        """
        workflow_id = request.data.get('workflow_id')
        step_node_id = request.data.get('step_node_id')
        workflow_run_id = request.data.get('workflow_run_id')

        logger.info(
            f"[V2] Execute single step request: workflow_id={workflow_id}, "
            f"step_node_id={step_node_id}, workflow_run_id={workflow_run_id}"
        )

        # Validate required fields
        if not workflow_id:
            return Response(
                {"error": "workflow_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not step_node_id:
            return Response(
                {"error": "step_node_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get workflow
        try:
            workflow = Workflow.active_objects.get(id=workflow_id, user=request.user)
        except Workflow.DoesNotExist:
            return Response(
                {"error": "Workflow not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get or create partial workflow run
        if workflow_run_id:
            try:
                workflow_run = WorkflowRun.objects.get(
                    id=workflow_run_id,
                    workflow=workflow,
                    user=request.user,
                    is_partial=True
                )
            except WorkflowRun.DoesNotExist:
                return Response(
                    {"error": "Partial workflow run not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            # Create new partial run
            workflow_run = WorkflowRun.objects.create(
                workflow=workflow,
                user=request.user,
                is_partial=True
            )

        # Validate that step_node_id exists in this workflow
        try:
            step_node = WorkflowNode.objects.get(
                workflow=workflow,
                node_id=step_node_id
            )
        except WorkflowNode.DoesNotExist:
            return Response(
                {"error": f"Step node {step_node_id} not found in workflow"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validate node configuration
        if step_node.node_type == 'step':
            node_errors = ExecutionValidator._validate_step_node(step_node)
        elif step_node.node_type == 'structuredOutput':
            node_errors = ExecutionValidator._validate_structured_output_node(step_node)
        else:
            node_errors = []

        if node_errors:
            return Response(
                {
                    "error": "Node validation failed. Please complete required fields.",
                    "validation_errors": node_errors,
                    "node_id": step_node_id
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get or create WorkflowRunStep for this node
        # (The step record will be used by the execution service)
        WorkflowRunStep.objects.get_or_create(
            workflow_run=workflow_run,
            step_node=step_node,
            defaults={
                'order': getattr(step_node.data_object, 'step_number', 0),
                'status': WorkflowRunStepStatus.PENDING
            }
        )

        # Execute single step using the service
        service = WorkflowExecutionService()

        try:
            # Run async execution in sync context using async_to_sync
            result = async_to_sync(service.execute_single_step)(
                workflow_run, step_node_id
            )

            # Refresh workflow_run to get latest state
            workflow_run.refresh_from_db()

            # V2: Return unified format with full workflow run data
            logger.info(
                f"[V2] Step {step_node_id} execution completed with success={result['success']}"
            )

            return Response({
                'success': result['success'],
                'workflow_run': WorkflowRunV2Serializer(workflow_run).data,
                'missing_dependencies': result.get('missing_dependencies', []),
                'error': result.get('error')
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(
                f"[V2] Exception during step {step_node_id} execution: {str(e)}",
                exc_info=True
            )
            return Response({
                'success': False,
                'workflow_run': WorkflowRunV2Serializer(workflow_run).data,
                'missing_dependencies': [],
                'error': f'Execution error: {str(e)}'
            }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='submit-human-validation')
    def submit_human_validation(self, request):
        """
        POST /api/v2/workflows/runs/submit-human-validation/

        Submit human validation choice for a structured output node.

        Request body:
        {
            "workflow_run_id": int,
            "node_id": str,
            "chosen_route": str
        }

        Response:
            WorkflowRunV2Serializer data with updated nodeStates
        """
        workflow_run_id = request.data.get('workflow_run_id')
        node_id = request.data.get('node_id')
        chosen_route = request.data.get('chosen_route')

        logger.info(
            f"[V2] Submit validation: run_id={workflow_run_id}, "
            f"node_id={node_id}, route={chosen_route}"
        )

        # Validate required fields
        if not all([workflow_run_id, node_id, chosen_route]):
            return Response(
                {"error": "workflow_run_id, node_id, and chosen_route are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get workflow run
        try:
            workflow_run = WorkflowRun.objects.get(
                id=workflow_run_id,
                user=request.user
            )
        except WorkflowRun.DoesNotExist:
            return Response(
                {"error": "Workflow run not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get the step waiting for validation
        try:
            step = WorkflowRunStep.objects.get(
                workflow_run=workflow_run,
                step_node__node_id=node_id,
                status=WorkflowRunStepStatus.PENDING_HUMAN_INPUT
            )
        except WorkflowRunStep.DoesNotExist:
            return Response(
                {"error": f"No pending validation found for node {node_id}"},
                status=status.HTTP_404_NOT_FOUND
            )

        logger.info(
            f"[V2] Human validation received: node={node_id}, route={chosen_route}"
        )

        # If this is a full run (not partial), resume execution
        # The execution service will handle updating the step status and metadata
        if not workflow_run.is_partial:
            logger.info(f"[V2] Resuming workflow run {workflow_run.id} after validation")
            enqueue(resume_workflow_run, workflow_run.id, node_id, chosen_route)
        else:
            # For partial runs (manual mode), update step metadata directly
            if not step.metadata:
                step.metadata = {}
            step.metadata[MetadataKey.USER_CHOICE] = chosen_route
            step.metadata[MetadataKey.SELECTED_ROUTE] = chosen_route
            step.metadata[MetadataKey.IS_HUMAN_VALIDATED] = True
            step.status = WorkflowRunStepStatus.COMPLETED
            step.response = chosen_route
            step.save()

        # Refresh workflow run to get latest state
        workflow_run.refresh_from_db()

        # Return updated workflow run with nodeStates
        return Response(WorkflowRunV2Serializer(workflow_run).data)
