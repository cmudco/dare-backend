from __future__ import annotations

from typing import Iterable

from workflows.constants import WorkflowRunStepStatus
from workflows.models import WorkflowRun, WorkflowRunStep


class RunStatusManager:
    """Single authority for persisting WorkflowRun status."""

    FINISHED_STEP_STATUSES = {
        WorkflowRunStepStatus.COMPLETED,
        WorkflowRunStepStatus.SKIPPED,
    }
    NON_TERMINAL_STEP_STATUSES = {
        WorkflowRunStepStatus.PENDING,
        WorkflowRunStepStatus.RUNNING,
        WorkflowRunStepStatus.PENDING_HUMAN_INPUT,
    }

    @classmethod
    def recompute(cls, run_or_id: WorkflowRun | int) -> str:
        """Recompute and persist the current run status from step state."""
        run_id = run_or_id.id if isinstance(run_or_id, WorkflowRun) else run_or_id
        run = cls._get_run(run_or_id)
        step_statuses = WorkflowRunStep.objects.filter(workflow_run_id=run_id).values_list(
            "status",
            flat=True,
        )
        status = cls._derive_status(step_statuses=step_statuses, is_partial=run.is_partial)
        WorkflowRun.objects.filter(id=run_id).update(status=status)
        if isinstance(run_or_id, WorkflowRun):
            run_or_id.status = status
        return status

    @classmethod
    def mark_failed(cls, run_or_id: WorkflowRun | int, *, error_message: str | None = None) -> str:
        """Force a run into a failed state by failing any non-terminal steps first."""
        run_id = run_or_id.id if isinstance(run_or_id, WorkflowRun) else run_or_id
        updates = {"status": WorkflowRunStepStatus.FAILED}
        if error_message is not None:
            updates["error"] = error_message
        WorkflowRunStep.objects.filter(
            workflow_run_id=run_id,
            status__in=cls.NON_TERMINAL_STEP_STATUSES,
        ).update(**updates)
        status = cls.recompute(run_or_id)
        if status != WorkflowRunStepStatus.FAILED:
            WorkflowRun.objects.filter(id=run_id).update(status=WorkflowRunStepStatus.FAILED)
            if isinstance(run_or_id, WorkflowRun):
                run_or_id.status = WorkflowRunStepStatus.FAILED
            return WorkflowRunStepStatus.FAILED
        return status

    @classmethod
    def _derive_status(cls, *, step_statuses: Iterable[str], is_partial: bool) -> str:
        statuses = list(step_statuses)
        if not statuses:
            return WorkflowRunStepStatus.RUNNING
        if WorkflowRunStepStatus.PENDING_HUMAN_INPUT in statuses:
            return WorkflowRunStepStatus.PENDING_HUMAN_INPUT
        if WorkflowRunStepStatus.FAILED in statuses:
            return WorkflowRunStepStatus.FAILED
        if WorkflowRunStepStatus.RUNNING in statuses:
            return WorkflowRunStepStatus.RUNNING
        if all(status in cls.FINISHED_STEP_STATUSES for status in statuses):
            return WorkflowRunStepStatus.COMPLETED
        if is_partial and any(status in cls.FINISHED_STEP_STATUSES for status in statuses):
            return WorkflowRunStepStatus.COMPLETED
        return WorkflowRunStepStatus.RUNNING

    @staticmethod
    def _get_run(run_or_id: WorkflowRun | int) -> WorkflowRun:
        if isinstance(run_or_id, WorkflowRun):
            return run_or_id
        return WorkflowRun.objects.only("id", "is_partial").get(id=run_or_id)
