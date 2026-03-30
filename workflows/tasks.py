"""
Workflow background tasks (RQ jobs).
"""
import logging
from typing import Optional

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import timezone
from django_rq import job

from conversations.socket_server import sio
from conversations.services.websocket_response_service import WebSocketResponseService
from files.constants import FileStatus
from files.models import File
from workflows.constants import BatchRunStatus
from workflows.models import BatchRun
from core.services.workflow_execution_service import WorkflowExecutionService
from workflows.services.workflow_run_repository import WorkflowRunRepository


logger = logging.getLogger(__name__)
User = get_user_model()


def _emit_to_user_room(user_id: int, event_data: dict) -> None:
    """Emit workflow event to user room synchronously."""
    room_name = f'workflow_user_{user_id}'
    try:
        async_to_sync(sio.emit)(
            'workflow_event',
            event_data,
            room=room_name,
            namespace='/workflow'
        )
    except Exception as exc:
        logger.warning(f"Failed to emit workflow_event to {room_name}: {exc}")


def _get_file_display_name(file_obj: Optional[File]) -> str:
    if not file_obj:
        return "Unknown file"
    return file_obj.name or file_obj.file.name


def _get_valid_file(file_id: int, user) -> Optional[File]:
    try:
        file_obj = File.active_objects.get(id=file_id, user=user)
    except File.DoesNotExist:
        return None

    if file_obj.status != FileStatus.PROCESSED:
        return None
    if file_obj.is_media:
        return None
    if file_obj.vector_db_source is not None and file_obj.vector_db_source != user.vector_db:
        return None

    return file_obj


def _finalize_batch_run(batch_run_id: int, user_id: int) -> None:
    batch_run = BatchRun.objects.filter(id=batch_run_id).first()
    if not batch_run:
        return

    if batch_run.completed_count + batch_run.failed_count < batch_run.total_files:
        return

    status = (
        BatchRunStatus.FAILED
        if batch_run.failed_count > 0
        else BatchRunStatus.COMPLETED
    )

    BatchRun.objects.filter(id=batch_run_id).update(
        status=status,
        ended_at=timezone.now()
    )

    _emit_to_user_room(
        user_id,
        WebSocketResponseService.format_batch_complete(
            batch_id=batch_run.id,
            completed_count=batch_run.completed_count,
            failed_count=batch_run.failed_count,
            total_files=batch_run.total_files
        )
    )


@job
def run_batch_workflow(
    batch_run_id: int,
    workflow_id: int,
    user_id: int,
    file_id: int,
    index: int,
    total: int
) -> None:
    """
    Execute a workflow run for a single file in a batch.
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning(f"Batch run user not found: {user_id}")
        return

    file_obj = _get_valid_file(file_id, user)
    if not file_obj:
        BatchRun.objects.filter(id=batch_run_id).update(
            failed_count=F('failed_count') + 1
        )
        _emit_to_user_room(
            user_id,
            WebSocketResponseService.format_batch_progress(
                batch_id=batch_run_id,
                index=index,
                total=total,
                file_id=file_id,
                file_name="Unknown file",
                status="failed"
            )
        )
        _finalize_batch_run(batch_run_id, user_id)
        return

    try:
        workflow_run = async_to_sync(WorkflowRunRepository.create_full_run)(
            workflow_id,
            user,
            ''
        )
    except Exception as e:
        logger.exception(f"Failed to create workflow run for batch: {e}")
        BatchRun.objects.filter(id=batch_run_id).update(
            failed_count=F('failed_count') + 1
        )
        _emit_to_user_room(
            user_id,
            WebSocketResponseService.format_batch_progress(
                batch_id=batch_run_id,
                index=index,
                total=total,
                file_id=file_id,
                file_name=_get_file_display_name(file_obj),
                status="failed"
            )
        )
        _finalize_batch_run(batch_run_id, user_id)
        return

    if not workflow_run:
        BatchRun.objects.filter(id=batch_run_id).update(
            failed_count=F('failed_count') + 1
        )
        _emit_to_user_room(
            user_id,
            WebSocketResponseService.format_batch_progress(
                batch_id=batch_run_id,
                index=index,
                total=total,
                file_id=file_id,
                file_name=_get_file_display_name(file_obj),
                status="failed"
            )
        )
        _finalize_batch_run(batch_run_id, user_id)
        return

    workflow_run.batch_run_id = batch_run_id
    workflow_run.batch_file_id = file_id
    workflow_run.save(update_fields=['batch_run_id', 'batch_file_id'])

    _emit_to_user_room(
        user_id,
        WebSocketResponseService.format_batch_progress(
            batch_id=batch_run_id,
            index=index,
            total=total,
            file_id=file_id,
            file_name=_get_file_display_name(file_obj),
            status="running",
            workflow_run_id=workflow_run.id
        )
    )

    try:
        result = async_to_sync(WorkflowExecutionService().execute_workflow)(
            workflow_run=workflow_run,
            send_callback=None,
            batch_file_id=file_id
        )
        success = result.success
    except Exception as e:
        logger.exception(f"Batch workflow execution error: {e}")
        success = False

    if success:
        BatchRun.objects.filter(id=batch_run_id).update(
            completed_count=F('completed_count') + 1
        )
    else:
        BatchRun.objects.filter(id=batch_run_id).update(
            failed_count=F('failed_count') + 1
        )

    _emit_to_user_room(
        user_id,
        WebSocketResponseService.format_batch_progress(
            batch_id=batch_run_id,
            index=index,
            total=total,
            file_id=file_id,
            file_name=_get_file_display_name(file_obj),
            status="completed" if success else "failed",
            workflow_run_id=workflow_run.id
        )
    )

    _finalize_batch_run(batch_run_id, user_id)
