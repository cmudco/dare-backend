"""
Subscription Manager

WebSocket room subscription and status retrieval for workflow runs.
Handles joining rooms and sending current state snapshots to subscribers.
"""

import logging
from typing import Dict, Any

from asgiref.sync import sync_to_async
from djangorestframework_camel_case.util import camelize

from workflows.api.serializers import WorkflowRunV2Serializer
from workflows.services.batch_executor import BatchExecutor
from workflows.services.workflow_run_repository import WorkflowRunRepository


logger = logging.getLogger(__name__)

# Fields to preserve from camelize() key mangling — these contain
# user-generated dict keys (node IDs) that must not be camelCase-converted.
_CAMELIZE_IGNORE_FIELDS = ('nodeStates',)


class SubscriptionManager:
    """WebSocket room subscription and status retrieval."""

    def __init__(self, sio, namespace: str = '/workflow'):
        self.sio = sio
        self.namespace = namespace
        self.batch = BatchExecutor(sio, namespace)

    async def subscribe_to_run(
        self,
        sid: str,
        run_id: int,
        user,
        session: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Subscribe to a workflow run room and return current status."""
        has_access = await WorkflowRunRepository.validate_access(run_id, user)
        if not has_access:
            return {'error': 'Workflow run not found or access denied'}

        room_name = f'workflow_run_{run_id}'
        await self.sio.enter_room(sid, room_name, namespace=self.namespace)
        session['subscriptions'].add(run_id)

        logger.info(f"Subscribed to workflow run: user={user.id}, run_id={run_id}")

        workflow_run = await WorkflowRunRepository.get_run_for_status(run_id)
        if workflow_run:
            run_status = await sync_to_async(
                lambda: {
                    'type': 'workflow_status',
                    **camelize(WorkflowRunV2Serializer(workflow_run).data,
                               ignore_fields=_CAMELIZE_IGNORE_FIELDS)
                }
            )()
            await self.sio.emit(
                'workflow_status',
                run_status,
                room=sid,
                namespace=self.namespace
            )

        return {'success': True, 'workflowRunId': run_id}

    async def subscribe_to_workflow(
        self,
        sid: str,
        workflow_id: int,
        user,
        session: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Subscribe to a workflow's latest run and return current execution state."""
        workflow_run = await WorkflowRunRepository.get_latest_run(workflow_id, user)

        latest_run_data = None
        if workflow_run:
            latest_run_data = await sync_to_async(
                lambda: camelize(WorkflowRunV2Serializer(workflow_run).data,
                                 ignore_fields=_CAMELIZE_IGNORE_FIELDS)
            )()
            run_id = latest_run_data.get('id')
            room_name = f'workflow_run_{run_id}'
            await self.sio.enter_room(sid, room_name, namespace=self.namespace)
            session['subscriptions'].add(run_id)
            logger.info(
                f"Subscribed to workflow {workflow_id} (run {run_id}): user={user.id}"
            )
        else:
            logger.info(
                f"Subscribed to workflow {workflow_id} (no runs yet): user={user.id}"
            )

        latest_batch_run = await self.batch.get_latest_summary(
            workflow_id=workflow_id,
            user=user
        )

        return {
            'success': True,
            'workflowId': workflow_id,
            'latestRun': latest_run_data,
            'latestBatchRun': latest_batch_run,
        }
