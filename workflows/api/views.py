import logging
import markdown
import os
import tempfile
import traceback

import weasyprint
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from common.permissions import IsOwner
from workflows.api.serializers import (
    WorkflowRunV2Serializer, WorkflowSerializer,
    WorkflowNodeSerializer, WorkflowEdgeSerializer,
)
from workflows.models import (
    Workflow, WorkflowRun, WorkflowRunStep,
    WorkflowNode, WorkflowEdge,
)
from workflows.handlers.utils.constants import NodeType
from workflows.services import WorkflowCloningService
from workflows.services.workflow_graph_service import WorkflowGraphService


logger = logging.getLogger(__name__)


class WorkflowViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting workflows.

    Supports ?shared=true query param to list published workflows from other users.
    """
    serializer_class = WorkflowSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        # Shared library: published workflows from other users
        shared = self.request.query_params.get('shared', None)
        if shared == 'true':
            return Workflow.active_objects.filter(
                is_published=True
            ).exclude(
                user=self.request.user
            ).select_related('user').prefetch_related(
                'nodes',
                'edges',
            ).order_by('-published_at')

        return Workflow.active_objects.filter(
            user=self.request.user
        ).prefetch_related(
            'nodes',
            'edges',
            Prefetch(
                'runs',
                queryset=WorkflowRun.active_objects.order_by('-created_at'),
                to_attr='_prefetched_runs'
            ),
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

            # Upsert nodes/edges via service layer
            nodes = request.data.get('nodes', None)
            if nodes is not None:
                WorkflowGraphService.upsert_nodes(instance, nodes)

            edges = request.data.get('edges', None)
            if edges is not None:
                WorkflowGraphService.upsert_edges(instance, edges)

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
        """Clone a workflow for the current user (same-user clone)."""
        instance = self.get_object()

        cloning_service = WorkflowCloningService()
        cloned_workflow = cloning_service.clone_workflow(instance)

        serializer = self.get_serializer(cloned_workflow)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='publish')
    def publish_workflow(self, request, pk=None):
        """Toggle the published status of a workflow.

        Only the owner can publish/unpublish. Mirrors the conversation
        publish pattern in ConversationViewSet.
        """
        try:
            instance = self.get_object()

            if instance.user != request.user:
                return Response(
                    {"error": "Permission denied"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Prevent publishing forked workflows
            if instance.file_owner_id is not None:
                return Response(
                    {"error": "Cannot publish forked workflows. Only original workflows can be published."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            instance.is_published = not instance.is_published
            instance.published_at = timezone.now() if instance.is_published else None
            instance.save(update_fields=['is_published', 'published_at', 'updated_at'])

            serializer = self.get_serializer(instance)
            return Response(serializer.data)

        except Exception as e:
            logger.error(f"Error publishing workflow {pk}: {str(e)}")
            return Response(
                {"error": f"Failed to publish workflow: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'], url_path='fork')
    def fork_workflow(self, request, pk=None):
        """Fork a published workflow for the current user.

        Creates a clone owned by the requesting user with file_owner_id
        set to the original owner for cross-user embedding/file access.
        """
        try:
            # Bypass IsOwner — look up directly by pk + is_published
            workflow = Workflow.active_objects.filter(
                pk=pk,
                is_published=True
            ).first()

            if not workflow:
                return Response(
                    {"error": "Workflow not found or not published"},
                    status=status.HTTP_404_NOT_FOUND
                )

            with transaction.atomic():
                cloning_service = WorkflowCloningService()
                forked_workflow = cloning_service.clone_workflow(
                    original=workflow,
                    target_user=request.user,
                    file_owner_id=workflow.user.id,
                )

            serializer = self.get_serializer(forked_workflow)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error forking workflow {pk}: {str(e)}")
            return Response(
                {"error": f"Failed to fork workflow: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

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


class WorkflowRunViewSet(viewsets.ModelViewSet):
    """
    Workflow Run ViewSet.

    Execution is handled via Socket.IO (/workflow namespace).
    This ViewSet provides:
    - CRUD operations for workflow runs
    - get-active-partial-run: Get current partial run state on page load
    - export-pdf: Export workflow run results as PDF
    """
    serializer_class = WorkflowRunV2Serializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        # Prefetch steps for NodeExecutionStateBuilder which builds nodeStates
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
                if target_node and target_node.node_type in [NodeType.CHAT_OUTPUT, NodeType.STRUCTURED_OUTPUT]:
                    executed_node_ids.append(edge.target)

        # Serialize the partial run using V2 serializer (nodeStates instead of steps)
        serializer = self.get_serializer(partial_run)

        return Response({
            'partialRun': serializer.data,
            'executedStepNodeIds': list(set(executed_node_ids))  # Remove duplicates
        }, status=200)

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
