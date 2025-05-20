from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from common.permissions import IsOwner
from workflows.api.serializers import WorkflowRunSerializer, WorkflowSerializer, StepSerializer
from workflows.constants import WorkflowRunStepStatus
from workflows.models import Workflow, Step, WorkflowRun, WorkflowRunStep
from django_rq import enqueue
from workflows.tasks import execute_workflow_run
from django.db.models import Subquery, OuterRef


class WorkflowViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating, updating and deleting workflows."""
    serializer_class = WorkflowSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Workflow.active_objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)

class StepViewSet(viewsets.ModelViewSet):
    """Endpoint for managing workflow steps."""
    serializer_class = StepSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Step.objects.filter(user=self.request.user).order_by('order')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

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

        workflow_run = WorkflowRun.objects.create(workflow=workflow, user=request.user)

        steps = workflow.steps.all().order_by('order')
        for idx, step in enumerate(steps, start=1):
            WorkflowRunStep.objects.create(
                workflow_run=workflow_run,
                step=step,
                order=idx,
                status=WorkflowRunStepStatus.PENDING
            )

        enqueue(execute_workflow_run, workflow_run.id)

        workflow_run.refresh_from_db()

        serializer = self.get_serializer(workflow_run)
        return Response(serializer.data, status=201)