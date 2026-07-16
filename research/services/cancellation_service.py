"""Durable, authorization-safe cancellation orchestration for research runs."""

from dataclasses import dataclass
from datetime import timedelta

import requests
from django.db import transaction
from django.utils import timezone

from research.constants import AgentRunStatus
from research.models import ResearchAgentRun
from research.services.hermes_service import (
    HermesStopResult,
    get_hermes_service,
    safe_hermes_usage,
)

CANCELLATION_ATTEMPT_LEASE = timedelta(seconds=90)
TERMINAL_STATUSES = {
    AgentRunStatus.COMPLETED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
}
ACTIVE_HERMES_STATUSES = {
    AgentRunStatus.STARTED,
    AgentRunStatus.QUEUED,
    AgentRunStatus.RUNNING,
    AgentRunStatus.WAITING_FOR_APPROVAL,
    AgentRunStatus.STOPPING,
}


@dataclass(frozen=True)
class TerminalRunResult:
    code: str
    status: str = ""
    data: dict | None = None


def _terminal_run_result(hermes, hermes_run_id):
    try:
        data = hermes.get_run(hermes_run_id)
    except requests.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        return TerminalRunResult(
            code=(
                "terminal_run_not_found"
                if status_code == 404
                else "terminal_http_error"
            )
        )
    except requests.Timeout:
        return TerminalRunResult(code="terminal_timeout")
    except requests.ConnectionError:
        return TerminalRunResult(code="terminal_connection_failure")
    except (requests.RequestException, ValueError):
        return TerminalRunResult(code="terminal_unavailable")

    if not isinstance(data, dict):
        return TerminalRunResult(code="terminal_invalid_response")
    return TerminalRunResult(
        code="terminal_received",
        status=str(data.get("status") or "").lower(),
        data=data,
    )


def _record_intent(run_id, user):
    """Commit first intent and return the owner-scoped locked run."""
    with transaction.atomic():
        run = (
            ResearchAgentRun.active_objects.select_for_update()
            .select_related("project")
            .get(id=run_id, project__user=user)
        )
        if run.status in TERMINAL_STATUSES:
            return run, False

        if run.cancellation_requested_at is None:
            run.cancellation_requested_at = timezone.now()
            run.cancellation_requested_by = user
            run.save(
                update_fields=[
                    "cancellation_requested_at",
                    "cancellation_requested_by",
                    "updated_at",
                ]
            )
        return run, True


def _claim_attempt(run_id):
    """Claim one upstream attempt under a short DB-backed concurrency lease."""
    with transaction.atomic():
        run = (
            ResearchAgentRun.active_objects.select_for_update()
            .select_related("project")
            .get(id=run_id)
        )
        if run.status in TERMINAL_STATUSES or not run.hermes_run_id:
            return None

        now = timezone.now()
        if (
            run.cancellation_last_attempt_at
            and now - run.cancellation_last_attempt_at < CANCELLATION_ATTEMPT_LEASE
        ):
            return None

        run.cancellation_attempt_count += 1
        run.cancellation_last_attempt_at = now
        run.save(
            update_fields=[
                "cancellation_attempt_count",
                "cancellation_last_attempt_at",
                "updated_at",
            ]
        )
        return run


def _apply_results(run_id, stop_result, terminal_result):
    """Apply safe stop evidence and authoritative terminal Hermes truth."""
    with transaction.atomic():
        run = ResearchAgentRun.active_objects.select_for_update().get(id=run_id)
        now = timezone.now()
        fields = {
            "cancellation_stop_http_status",
            "cancellation_error_code",
            "cancellation_error_detail",
            "updated_at",
        }
        run.cancellation_stop_http_status = stop_result.http_status
        if stop_result.acknowledged and terminal_result.code != "terminal_received":
            run.cancellation_error_code = terminal_result.code
            run.cancellation_error_detail = "Hermes acknowledged the stop request, but terminal truth is unavailable."
        else:
            run.cancellation_error_code = (
                "" if stop_result.acknowledged else stop_result.code
            )
            run.cancellation_error_detail = stop_result.detail[:255]
        if stop_result.acknowledged and run.cancellation_acknowledged_at is None:
            run.cancellation_acknowledged_at = now
            fields.add("cancellation_acknowledged_at")

        terminal_status = terminal_result.status
        terminal_data = terminal_result.data or {}
        if (
            terminal_result.code == "terminal_received"
            and terminal_status == AgentRunStatus.COMPLETED
        ):
            run.status = AgentRunStatus.COMPLETED
            run.status_detail = "Hermes confirmed that the run completed."
            run.completed_at = run.completed_at or now
            fields.update({"status", "status_detail", "completed_at"})
        elif (
            terminal_result.code == "terminal_received"
            and terminal_status == AgentRunStatus.FAILED
        ):
            run.status = AgentRunStatus.FAILED
            run.status_detail = "Hermes confirmed that the run failed."
            run.error = "Hermes reported a failed run."
            run.completed_at = run.completed_at or now
            fields.update({"status", "status_detail", "error", "completed_at"})
        elif (
            terminal_result.code == "terminal_received"
            and terminal_status == AgentRunStatus.CANCELLED
        ):
            run.status = AgentRunStatus.CANCELLED
            run.status_detail = "Hermes confirmed that the run was cancelled."
            run.completed_at = run.completed_at or now
            run.cancellation_confirmed_at = run.cancellation_confirmed_at or now
            fields.update(
                {"status", "status_detail", "completed_at", "cancellation_confirmed_at"}
            )
        elif (
            terminal_result.code == "terminal_received"
            and terminal_status in ACTIVE_HERMES_STATUSES
        ):
            if run.status not in TERMINAL_STATUSES:
                run.status = terminal_status
                run.status_detail = (
                    f"Cancellation requested; Hermes still reports {terminal_status}."
                )
                run.completed_at = None
                fields.update({"status", "status_detail", "completed_at"})
        elif run.status not in TERMINAL_STATUSES:
            run.status = AgentRunStatus.OUTCOME_UNKNOWN
            run.status_detail = (
                "Cancellation requested; Hermes terminal outcome is unavailable."
            )
            run.completed_at = None
            fields.update({"status", "status_detail", "completed_at"})

        usage = safe_hermes_usage(terminal_data.get("usage"))
        if usage and terminal_status in TERMINAL_STATUSES:
            run.usage = usage
            fields.add("usage")
        run.save(update_fields=sorted(fields))
        return run


def execute_pending_cancellation(run_id, *, hermes=None):
    """Execute a claimed attempt with no database transaction held over I/O."""
    claimed = _claim_attempt(run_id)
    if claimed is None:
        return ResearchAgentRun.active_objects.get(id=run_id)

    hermes = hermes or get_hermes_service(claimed.project)
    stop_result = hermes.stop_run(claimed.hermes_run_id)
    terminal_result = _terminal_run_result(hermes, claimed.hermes_run_id)
    return _apply_results(claimed.id, stop_result, terminal_result)


def request_run_cancellation(run_id, user):
    """Persist authorized intent, then best-effort stop and verify Hermes."""
    run, should_continue = _record_intent(run_id, user)
    if not should_continue:
        return run, False
    if not run.hermes_run_id:
        return run, True
    return execute_pending_cancellation(run.id), True
