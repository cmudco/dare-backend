from typing import Optional
import asyncio
import logging
from django_rq import job, enqueue
from channels.db import database_sync_to_async
from django.utils import timezone
from billing.models import Transaction

from conversations.models import LLM
from core.services.llm_service import LLMService
from .models import WorkflowRun, WorkflowRunStep
from .constants import WorkflowRunStepStatus
from .models import WorkflowNode, StepNodeData
from core.services.file_processor import FileProcessor

# Import new node handler-based execution service
from core.services.workflow_execution_service import WorkflowExecutionService


def get_previous_step_node(current_step_node: WorkflowNode, workflow) -> Optional[WorkflowNode]:
    """
    Get the previous step node in the workflow based on step_number.
    """
    if not current_step_node.data_object or not isinstance(current_step_node.data_object, StepNodeData):
        return None

    current_step_number = current_step_node.data_object.step_number

    # TODO: This legacy function uses data_object queries that don't work with GenericForeignKey
    # Since we're now using node handlers, this function may not be needed
    # Commenting out the problematic query for now
    previous_step = None  # workflow.nodes.filter(node_type='step', data_object__step_number__lt=current_step_number).order_by('-data_object__step_number').first()

    return previous_step

async def execute_step_async(step_node: 'WorkflowNode', previous_response: Optional[str] = None, workflow_run_step_obj=None) -> str:
    """
    Execute a single step node of a workflow.

    Args:
        step_node: The WorkflowNode (step type) to execute
        previous_response: Response from the previous step (if any)
        workflow_run_step_obj: The WorkflowRunStep object for saving snippets

    Returns:
        The response from the LLM
    """
    try:
        # Get the StepNodeData from the WorkflowNode
        step_data = await database_sync_to_async(lambda sn: sn.data_object if sn.node_type == 'step' else None)(step_node)
        if not step_data or not isinstance(step_data, StepNodeData):
            raise ValueError(f"WorkflowNode {step_node.node_id} is not a valid step node")

        # Get step configuration from StepNodeData
        step_prompt = await database_sync_to_async(lambda sd: sd.prompt)(step_data)
        prompt_id = await database_sync_to_async(lambda p: p.id if p else None)(step_prompt)

        if previous_response:
            message = previous_response
        else:
            prompt_content = await database_sync_to_async(lambda p: p.content if p else "")(step_prompt)
            message = prompt_content

        step_llm_obj = await database_sync_to_async(lambda sd: sd.llm)(step_data)
        if step_llm_obj:
            llm_to_use = step_llm_obj
        else:
            llm_to_use = await database_sync_to_async(LLM.objects.filter(provider="openai").first)()

        step_max_tokens = await database_sync_to_async(lambda sd: sd.max_tokens)(step_data)
        step_temperature = await database_sync_to_async(lambda sd: sd.temperature)(step_data)
        step_max_context_snippets = await database_sync_to_async(lambda sd: sd.max_context_snippets)(step_data)
        step_document_similarity_threshold = await database_sync_to_async(lambda sd: sd.document_similarity_threshold)(step_data)

        llm_service = LLMService()

        file_ids = None
        embedding_ids = None

        # Get effective files and embeddings from StepNodeData
        step_files = await database_sync_to_async(lambda sd: list(sd.content_files.values_list('id', flat=True)))(step_data)
        step_embeddings = await database_sync_to_async(lambda sd: list(sd.embedding_files.values_list('id', flat=True)))(step_data)

        # Handle previous step file/embedding inheritance
        use_previous_step_files = await database_sync_to_async(lambda sd: sd.use_previous_step_files)(step_data)
        use_previous_step_embeddings = await database_sync_to_async(lambda sd: sd.use_previous_step_embeddings)(step_data)

        if use_previous_step_files or use_previous_step_embeddings:
            workflow = await database_sync_to_async(lambda wr: wr.workflow_run.workflow)(workflow_run_step_obj)
            previous_step_node = await database_sync_to_async(get_previous_step_node)(step_node, workflow)

            if previous_step_node and previous_step_node.data_object:
                if use_previous_step_files:
                    prev_files = await database_sync_to_async(lambda sd: list(sd.content_files.values_list('id', flat=True)))(previous_step_node.data_object)
                    step_files.extend(prev_files)
                if use_previous_step_embeddings:
                    prev_embeddings = await database_sync_to_async(lambda sd: list(sd.embedding_files.values_list('id', flat=True)))(previous_step_node.data_object)
                    step_embeddings.extend(prev_embeddings)

        if step_files:
            file_ids = step_files

        if step_embeddings:
            embedding_ids = step_embeddings

        # Get workflow user
        workflow = await database_sync_to_async(lambda sn: sn.workflow)(step_node)
        step_user = await database_sync_to_async(lambda w: w.user)(workflow)
        step_user_id = await database_sync_to_async(lambda u: u.id)(step_user)

        response_generator = llm_service.query(
            message=message,
            conversation=None,
            llm=llm_to_use,
            file_ids=file_ids,
            embedding_ids=embedding_ids,
            user_id=step_user_id,
            prompt_id=prompt_id,
            message_obj=None,
            workflow_run_step_obj=workflow_run_step_obj,
            max_tokens=step_max_tokens,
            temperature=step_temperature,
            max_context_snippets=step_max_context_snippets,
            document_similarity_threshold=step_document_similarity_threshold
        )

        full_response = ""
        token_usage = {}
        async for chunk, usage in response_generator:
            full_response += chunk
            if usage:
                token_usage = usage

        if token_usage and llm_to_use:
            input_tokens = token_usage.get("input_tokens", 0)
            output_tokens = token_usage.get("output_tokens", 0)

            try:
                await database_sync_to_async(create_workflow_transaction)(
                    user=step_user,
                    llm=llm_to_use,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    step_node_id=step_node.id
                )
            except Exception as billing_error:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Billing error in execute_step_async: {str(billing_error)}")

        return full_response
    except Exception as e:
        raise

def execute_step(step_node: WorkflowNode, previous_response: Optional[str] = None, workflow_run_step_obj=None) -> str:
    """
    Synchronous wrapper for execute_step_async to be used in RQ jobs.

    Args:
        step_node (WorkflowNode): The step node to execute.
        previous_response (Optional[str]): The response from the previous step, if applicable.
        workflow_run_step_obj: The WorkflowRunStep object for saving snippets

    Returns:
        str: The generated AI response.
    """
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(execute_step_async(step_node, previous_response, workflow_run_step_obj))
        loop.close()
        return result
    except Exception as e:
        raise

@job('default', timeout=600)
def execute_workflow_run(workflow_run_id):
    """Execute workflow using new graph-based execution engine."""
    logger = logging.getLogger(__name__)

    try:
        workflow_run = WorkflowRun.active_objects.get(id=workflow_run_id)
    except WorkflowRun.DoesNotExist:
        logger.error(f"Workflow run {workflow_run_id} not found")
        return

    try:
        # Use the new graph-based execution service
        logger.info(f"Starting graph-based execution for workflow run {workflow_run_id}")

        # Run the async execution in a new event loop
        def run_workflow_execution():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                service = WorkflowExecutionService()
                result = loop.run_until_complete(service.execute_workflow(workflow_run))
                return result
            finally:
                loop.close()

        execution_result = run_workflow_execution()

        if execution_result['success']:
            logger.info(f"Workflow run {workflow_run_id} completed successfully")
        else:
            logger.error(f"Workflow run {workflow_run_id} failed: {execution_result.get('error', 'Unknown error')}")

    except Exception as e:
        logger.error(f"Workflow execution failed: {str(e)}", exc_info=True)
        # Mark workflow as failed
        try:
            workflow_run.ended_at = timezone.now()
            workflow_run.save(update_fields=['ended_at'])
        except:
            pass

@job('default', timeout=600)
def execute_step_task(workflow_run_step_id, workflow_run_id=None):
    """
    Legacy single step execution task - maintained for backward compatibility.
    New workflows should use execute_workflow_run which uses graph-based execution.
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Executing individual step task {workflow_run_step_id} (legacy mode)")

    try:
        step_run = WorkflowRunStep.objects.get(id=workflow_run_step_id)
        step_run.status = WorkflowRunStepStatus.RUNNING
        step_run.save()

        try:
            response = execute_step(step_run.step_node, workflow_run_step_obj=step_run)
            step_run.response = response
            step_run.status = WorkflowRunStepStatus.COMPLETED

            # Get user from workflow instead of step
            workflow_user = step_run.step_node.workflow.user
            transaction = Transaction.objects.filter(
                user=workflow_user,
                message__contains=f"Workflow step {step_run.step_node.id}"
            ).order_by('-created_at').first()

            if transaction:
                step_run.input_tokens = transaction.input_tokens
                step_run.output_tokens = transaction.output_tokens

        except Exception as e:
            step_run.error = str(e)
            step_run.status = WorkflowRunStepStatus.FAILED
        finally:
            step_run.save()

            if workflow_run_id:
                # Use atomic transaction with select_for_update to prevent race conditions
                from django.db import transaction

                with transaction.atomic():
                    workflow_run = WorkflowRun.active_objects.select_for_update().get(id=workflow_run_id)
                    # Refresh step data to ensure we have the latest status updates
                    pending_steps = workflow_run.steps.filter(
                        status__in=[WorkflowRunStepStatus.PENDING, WorkflowRunStepStatus.RUNNING]
                    )

                    if not pending_steps.exists():
                        workflow_run.ended_at = timezone.now()
                        workflow_run.save(update_fields=['ended_at'])

    except WorkflowRunStep.DoesNotExist:
        logger.error(f"WorkflowRunStep {workflow_run_step_id} not found")

def create_workflow_transaction(user, llm, input_tokens, output_tokens, step_node_id):
    from core.services.billing_service import BillingService

    billing_service = BillingService()
    return billing_service.process_workflow_billing(
        user=user,
        llm=llm,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        step_node_id=step_node_id
    )


# Convenience functions for graph-based execution
def execute_workflow_graph_sync(workflow_run: WorkflowRun) -> dict:
    """
    Synchronous wrapper for graph-based workflow execution.

    Args:
        workflow_run: The workflow run to execute

    Returns:
        Dict containing execution results
    """
    import asyncio

    def run_async_execution():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            service = WorkflowExecutionService()
            return loop.run_until_complete(service.execute_workflow(workflow_run))
        finally:
            loop.close()

    return run_async_execution()


def validate_workflow_structure(workflow) -> list:
    """
    Validate a workflow structure by checking for required nodes.

    Args:
        workflow: The workflow to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    try:
        errors = []

        # Check for at least one start node
        start_nodes = workflow.nodes.filter(node_type='start')
        if not start_nodes.exists():
            errors.append("Workflow must have at least one start node")
        elif start_nodes.count() > 1:
            errors.append("Workflow should have only one start node")

        # Check for at least one step node
        step_nodes = workflow.nodes.filter(node_type='step')
        if not step_nodes.exists():
            errors.append("Workflow must have at least one step node")

        return errors
    except Exception as e:
        return [f"Validation failed: {str(e)}"]


def get_workflow_summary(workflow) -> dict:
    """
    Get summary of a workflow structure.

    Args:
        workflow: The workflow to analyze

    Returns:
        Dict containing workflow structure summary
    """
    try:
        nodes = workflow.nodes.all()
        node_counts = {}
        for node in nodes:
            node_counts[node.node_type] = node_counts.get(node.node_type, 0) + 1

        validation_errors = validate_workflow_structure(workflow)

        return {
            'total_nodes': nodes.count(),
            'node_types': node_counts,
            'is_valid': len(validation_errors) == 0,
            'validation_errors': validation_errors
        }
    except Exception as e:
        return {
            'error': f"Failed to analyze workflow: {str(e)}",
            'is_valid': False
        }