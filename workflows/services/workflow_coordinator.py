"""
Workflow Coordinator — Thin Facade

Delegates all workflow operations to focused executors.
Preserves the same public API so WorkflowNamespace requires zero changes.
"""

import logging
from typing import Dict, Any, Optional, List

from workflows.services.batch_executor import BatchExecutor
from workflows.services.live_executor import LiveExecutor
from workflows.services.single_step_executor import SingleStepExecutor
from workflows.services.subscription_manager import SubscriptionManager


logger = logging.getLogger(__name__)


class WorkflowCoordinator:
    """Thin facade delegating to focused executors."""

    def __init__(self, sio, namespace: str = '/workflow'):
        self.live = LiveExecutor(sio, namespace)
        self.single_step = SingleStepExecutor(sio, namespace)
        self.batch = BatchExecutor(sio, namespace)
        self.subscriptions = SubscriptionManager(sio, namespace)

    async def start_execution(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_run_id: Optional[int] = None,
        workflow_id: Optional[int] = None,
        user_input: str = ''
    ) -> Dict[str, Any]:
        return await self.live.start(
            sid, user, session,
            workflow_run_id=workflow_run_id,
            workflow_id=workflow_id,
            user_input=user_input
        )

    async def execute_single_step(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_id: int,
        step_node_id: str,
        workflow_run_id: Optional[int] = None
    ) -> Dict[str, Any]:
        return await self.single_step.execute(
            sid, user, session,
            workflow_id=workflow_id,
            step_node_id=step_node_id,
            workflow_run_id=workflow_run_id
        )

    async def start_batch_execution(
        self,
        sid: str,
        user,
        session: Dict[str, Any],
        workflow_id: Optional[int],
        file_ids: List[int]
    ) -> Dict[str, Any]:
        return await self.batch.start(
            sid, user, session,
            workflow_id=workflow_id,
            file_ids=file_ids
        )

    async def subscribe_workflow(
        self,
        sid: str,
        workflow_id: int,
        user,
        session: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self.subscriptions.subscribe_to_workflow(
            sid, workflow_id, user, session
        )

    async def subscribe_workflow_run(
        self,
        sid: str,
        run_id: int,
        user,
        session: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await self.subscriptions.subscribe_to_run(
            sid, run_id, user, session
        )

    async def submit_validation(
        self,
        user,
        workflow_run_id: int,
        node_id: str,
        selected_route: str,
        continue_execution: bool = True
    ) -> Dict[str, Any]:
        return await self.live.submit_validation(
            user, workflow_run_id, node_id, selected_route, continue_execution
        )
