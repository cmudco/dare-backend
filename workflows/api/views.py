from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from common.permissions import IsOwner
from workflows.api.serializers import WorkflowRunSerializer, WorkflowSerializer, StepSerializer
from workflows.constants import WorkflowRunStepStatus
from workflows.models import Workflow, Step, WorkflowRun, WorkflowRunStep
from django_rq import enqueue
from workflows.tasks import execute_workflow_run
from django.db.models import Subquery, OuterRef
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

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'], url_path='clone')
    def clone_workflow(self, request, pk=None):
        """Custom action to clone a workflow."""
        instance = self.get_object()
        
        cloned_workflow = Workflow(
            user=instance.user,
            title=f"COPY OF - {instance.title}",
            description=instance.description,
            mode=instance.mode,
            version=1,
            parent=None
        )
        cloned_workflow.save()

        for step in instance.steps.all():
            cloned_step = Step.objects.create(
                user=step.user,
                prompt=step.prompt,
                order=step.order,
                llm=step.llm,
                max_tokens=step.max_tokens,
                temperature=step.temperature,
                max_context_snippets=step.max_context_snippets,
                document_similarity_threshold=step.document_similarity_threshold,
            )
            
            # Clone the many-to-many relationships for files and embeddings
            if hasattr(step, 'files'):
                cloned_step.files.set(step.files.all())
            if hasattr(step, 'embeddings'):
                cloned_step.embeddings.set(step.embeddings.all())
                
            cloned_workflow.steps.add(cloned_step)

        serializer = self.get_serializer(cloned_workflow)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

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

        if not workflow.steps.exists():
            return Response(
                {"error": "Cannot run workflow with zero steps. Please add at least one step to the workflow."}, 
                status=400
            )

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

    @action(detail=True, methods=['get'], url_path='export-pdf')
    def export_pdf(self, request, pk=None):
        """Export workflow run results as a PDF."""
        print(f"PDF export requested for workflow run ID: {pk}")
        print(f"User: {request.user}")
        
        try:
            workflow_run = self.get_object()
            print(f"Found workflow run: {workflow_run}")
            
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
            
            print(f"Context prepared with {len(context['steps'])} steps")
            
            # Render HTML template
            html_content = render_to_string('workflows/pdf_export.html', context)
            print(f"HTML template rendered, length: {len(html_content)}")
            
            # Generate PDF
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                weasyprint.HTML(string=html_content).write_pdf(tmp_file.name)
                
                # Read PDF content
                with open(tmp_file.name, 'rb') as pdf_file:
                    pdf_content = pdf_file.read()
                
                # Clean up temporary file
                os.unlink(tmp_file.name)
            
            print(f"PDF generated successfully, size: {len(pdf_content)} bytes")
            
            # Prepare response
            filename = f"{workflow_run.workflow.title.replace(' ', '_')}-results.pdf"
            response = HttpResponse(pdf_content, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(pdf_content)
            
            return response
            
        except Exception as e:
            print(f"PDF export error: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {'error': f'Failed to generate PDF: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )