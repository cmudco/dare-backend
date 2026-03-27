"""
Batch Executor

Batch file execution management. Validates files, creates BatchRun,
and enqueues individual workflow runs via Django RQ.
"""

import logging
from datetime import timedelta
from typing import Dict, Any, Optional, List

from asgiref.sync import sync_to_async
from django.utils import timezone
from django_rq import get_queue

from conversations.services.websocket_response_service import WebSocketResponseService
from files.constants import FileStatus
from files.models import File
from workflows.constants import BatchRunStatus, WorkflowRunStepStatus
from workflows.models import BatchRun, WorkflowRun
from workflows.services.workflow_run_repository import (
    WorkflowRunRepository,
    STALE_RUN_THRESHOLD_MINUTES,
)
from workflows.tasks import run_batch_workflow


logger = logging.getLogger(__name__)


class BatchExecutor:
    """Batch file execution management."""

    def __init__(self, sio, namespace: str = '/workflow'):
        self.sio = sio
        self.namespace = namespace

    async def start(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_id: Optional[int],
        file_ids: List[int]
    ) -> Dict[str, Any]:
        """Start batch execution by enqueuing a workflow run for each file."""
        if not workflow_id:
            return {'error': 'Missing workflowId'}
        if not file_ids:
            return {'error': 'No files selected for batch execution'}

        workflow = await WorkflowRunRepository.get_workflow(workflow_id, user)
        if not workflow:
            return {'error': 'Workflow not found or access denied'}

        valid_files, invalid_ids = await self._get_valid_files(user, file_ids)
        if invalid_ids:
            return {
                'error': 'Some files are not eligible for batch execution',
                'invalidFileIds': invalid_ids
            }

        batch_run = await sync_to_async(
            lambda: BatchRun.objects.create(
                workflow=workflow,
                user=user,
                total_files=len(valid_files)
            )
        )()

        room_name = f'workflow_user_{user.id}'
        await self.sio.emit(
            'workflow_event',
            WebSocketResponseService.format_batch_started(
                batch_id=batch_run.id,
                total_files=len(valid_files),
                workflow_id=workflow_id
            ),
            room=room_name,
            namespace=self.namespace
        )

        queue = get_queue()
        for index, file_obj in enumerate(valid_files, start=1):
            queue.enqueue(
                run_batch_workflow,
                batch_run.id,
                workflow_id,
                user.id,
                file_obj.id,
                index,
                len(valid_files)
            )

        logger.info(
            f"Started batch execution: user={user.id}, workflow_id={workflow_id}, "
            f"batch_id={batch_run.id}, total_files={len(valid_files)}"
        )
        return {'success': True, 'batchId': batch_run.id}

    async def get_latest_summary(
        self,
        workflow_id: int,
        user
    ) -> Optional[Dict[str, Any]]:
        """Return summary of latest batch run for a workflow (if any)."""
        def _fetch_summary():
            batch_run = (
                BatchRun.objects.filter(workflow_id=workflow_id, user=user)
                .order_by('-created_at')
                .first()
            )
            if not batch_run:
                return None

            # Clean up stale batch runs stuck in "running" state
            if batch_run.status == BatchRunStatus.RUNNING:
                stale_threshold = timezone.now() - timedelta(
                    minutes=STALE_RUN_THRESHOLD_MINUTES
                )
                if batch_run.created_at < stale_threshold:
                    logger.warning(
                        f"Cleaning up stale batch run {batch_run.id} for workflow {workflow_id} "
                        f"(created at {batch_run.created_at})"
                    )
                    BatchRun.objects.filter(id=batch_run.id).update(
                        status=BatchRunStatus.FAILED,
                        ended_at=timezone.now()
                    )
                    batch_run.refresh_from_db()

            runs = (
                WorkflowRun.objects.filter(batch_run=batch_run)
                .select_related('batch_file')
                .prefetch_related('steps')
                .order_by('created_at')
            )

            file_statuses = []
            for index, run in enumerate(runs, start=1):
                file_obj = run.batch_file
                file_name = "Unknown file"
                if file_obj:
                    file_name = file_obj.name or file_obj.file.name

                if run.status in (
                    WorkflowRunStepStatus.RUNNING,
                    WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
                ):
                    status = 'running'
                elif run.status == WorkflowRunStepStatus.FAILED:
                    status = 'failed'
                else:
                    status = 'completed'

                file_statuses.append({
                    'fileId': run.batch_file_id or 0,
                    'fileName': file_name,
                    'status': status,
                    'workflowRunId': run.id,
                    'index': index,
                })

            return {
                'batchId': batch_run.id,
                'workflowId': workflow_id,
                'status': batch_run.status,
                'totalFiles': batch_run.total_files,
                'completedCount': batch_run.completed_count,
                'failedCount': batch_run.failed_count,
                'fileStatuses': file_statuses,
            }

        return await sync_to_async(_fetch_summary)()

    # ==================== Internal ====================

    @staticmethod
    async def _get_valid_files(
        user,
        file_ids: List[int]
    ) -> tuple[List[File], List[int]]:
        """Validate batch files and return ordered list with invalid IDs."""
        if not file_ids:
            return [], []

        def _fetch_files():
            files = list(
                File.active_objects.filter(
                    id__in=file_ids,
                    user=user,
                    status=FileStatus.PROCESSED,
                    is_media=False
                )
            )
            file_map = {file_obj.id: file_obj for file_obj in files if (
                file_obj.vector_db_source is None or file_obj.vector_db_source == user.vector_db
            )}
            ordered_files: List[File] = []
            invalid_ids: List[int] = []
            seen_ids = set()
            for file_id in file_ids:
                if file_id in seen_ids:
                    continue
                seen_ids.add(file_id)
                if file_id in file_map:
                    ordered_files.append(file_map[file_id])
                else:
                    invalid_ids.append(file_id)
            return ordered_files, invalid_ids

        return await sync_to_async(_fetch_files)()
