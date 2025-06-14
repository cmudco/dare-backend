from typing import Optional
import asyncio
import logging
from django_rq import job, enqueue
from channels.db import database_sync_to_async
from django.utils import timezone
from billing.models import Transaction

from conversations.models import LLM
from core.services.llm_service import LLMService
from .models import Step, WorkflowRun, WorkflowRunStep, Mode, WorkflowRunStepStatus
from core.services.file_processor import FileProcessor

async def execute_step_async(step: 'Step', previous_response: Optional[str] = None) -> str:
    """
    Executes a single step in a workflow asynchronously.

    Args:
        step (Step): The step to execute.
        previous_response (Optional[str]): The response from the previous step, if applicable.

    Returns:
        str: The generated AI response.
    """
    try:
        step_prompt = await database_sync_to_async(lambda s: s.prompt)(step)
        prompt_id = await database_sync_to_async(lambda p: p.id if p else None)(step_prompt)

        if previous_response:
            message = previous_response
        else:
            prompt_content = await database_sync_to_async(lambda p: p.content if p else "")(step_prompt)
            message = prompt_content

        step_llm_obj = await database_sync_to_async(lambda s: s.llm)(step)
        if step_llm_obj:
            llm_to_use = step_llm_obj
        else:
            llm_to_use = await database_sync_to_async(LLM.objects.filter(provider="openai").first)()
        step_max_tokens = await database_sync_to_async(lambda s: s.max_tokens)(step)
        step_temperature = await database_sync_to_async(lambda s: s.temperature)(step)
        step_max_context_snippets = await database_sync_to_async(lambda s: s.max_context_snippets)(step)
        step_document_similarity_threshold = await database_sync_to_async(lambda s: s.document_similarity_threshold)(step)

        llm_service = LLMService()

        file_ids = None
        embedding_ids = None
        
        step_files = await database_sync_to_async(lambda s: list(s.files.values_list('id', flat=True)))(step)

        step_embeddings = await database_sync_to_async(lambda s: list(s.embeddings.values_list('id', flat=True)))(step)


        if step_files:
            file_ids = step_files

        if step_embeddings:
            embedding_ids = step_embeddings

        step_user = await database_sync_to_async(lambda s: s.user)(step)
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
                    step_id=step.id
                )
            except Exception as billing_error:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Billing error in execute_step_async: {str(billing_error)}")

        return full_response
    except Exception as e:
        raise

def execute_step(step: 'Step', previous_response: Optional[str] = None) -> str:
    """
    Synchronous wrapper for execute_step_async to be used in RQ jobs.

    Args:
        step (Step): The step to execute.
        previous_response (Optional[str]): The response from the previous step, if applicable.

    Returns:
        str: The generated AI response.
    """
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(execute_step_async(step, previous_response))
        loop.close()
        return result
    except Exception as e:
        raise

@job('default', timeout=600)
def execute_workflow_run(workflow_run_id):
    try:
        workflow_run = WorkflowRun.active_objects.get(id=workflow_run_id)
    except WorkflowRun.DoesNotExist:
        return

    try:
        workflow = workflow_run.workflow
        if workflow.mode == Mode.SERIAL:
            previous_response = None
            for step_run in workflow_run.steps.all().order_by('order'):
                step_run.status = WorkflowRunStepStatus.RUNNING
                step_run.save()

                try:
                    response = execute_step(step_run.step, previous_response)
                    step_run.response = response
                    step_run.status = WorkflowRunStepStatus.COMPLETED
                    previous_response = response
                except Exception as e:
                    step_run.error = str(e)
                    step_run.status = WorkflowRunStepStatus.FAILED
                finally:
                    step_run.save()

            workflow_run.ended_at = timezone.now()
            workflow_run.save(update_fields=['ended_at'])

        elif workflow.mode == Mode.PARALLEL:
            for step_run in workflow_run.steps.all():
                enqueue(execute_step_task, step_run.id, workflow_run.id)

    except Exception as e:
        workflow_run.ended_at = timezone.now()
        workflow_run.save(update_fields=['ended_at'])

@job('default', timeout=600)
def execute_step_task(workflow_run_step_id, workflow_run_id=None):
    try:
        step_run = WorkflowRunStep.objects.get(id=workflow_run_step_id)
        step_run.status = WorkflowRunStepStatus.RUNNING
        step_run.save()

        try:
            response = execute_step(step_run.step)
            step_run.response = response
            step_run.status = WorkflowRunStepStatus.COMPLETED

            transaction = Transaction.objects.filter(
                user=step_run.step.user,
                message__contains=f"Workflow step {step_run.step.id}"
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
                workflow_run = WorkflowRun.active_objects.get(id=workflow_run_id)
                all_steps = workflow_run.steps.all()
                pending_steps = all_steps.filter(status__in=[WorkflowRunStepStatus.PENDING, WorkflowRunStepStatus.RUNNING])

                if not pending_steps.exists():
                    workflow_run.ended_at = timezone.now()
                    workflow_run.save(update_fields=['ended_at'])

    except WorkflowRunStep.DoesNotExist:
        pass

def create_workflow_transaction(user, llm, input_tokens, output_tokens, step_id):
    from core.services.billing_service import BillingService

    billing_service = BillingService()
    return billing_service.process_workflow_billing(
        user=user,
        llm=llm,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        step_id=step_id
    )