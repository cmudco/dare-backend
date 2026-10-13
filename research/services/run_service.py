"""
The one path every agent run takes on Hermes: start, stream, settle.

Chat turns, Scouts, the Critic and the Presenter differ only in their prompt
and in what they do with the final text.
"""

import logging
import time

import requests
from django.utils import timezone

from research.constants import (
    LIVE_RUN_STATUSES,
    AgentRole,
    AgentRunStatus,
    AgentToolCallStatus,
)
from research.models import ResearchAgentRun
from research.services.audit_service import record_tool_call
from research.services.cancellation_service import execute_pending_cancellation
from research.services.cost_service import set_run_cost
from research.services.hermes_service import safe_hermes_usage
from research.services.learning_service import sync_pending_learning
from research.services.profile_service import profile_name_for, write_profile_files
from research.services.prompt_builder import profile_files
from research.services.thread_service import on_run_finished

logger = logging.getLogger(__name__)

_START_ATTEMPTS = 3
_POLL_SECONDS = 5
_TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


def set_detail(run, detail):
    run.status_detail = detail[:255]
    run.save(update_fields=["status_detail", "updated_at"])


def settle(run, status, detail, *, terminal=False, usage=None, error=""):
    """Persist a run's state as Hermes reported it (or as unconfirmed)."""
    run.status = status
    run.status_detail = detail[:255]
    run.completed_at = timezone.now() if terminal else None
    fields = ["status", "status_detail", "completed_at", "updated_at"]
    if usage:
        run.usage = usage
        fields += ["usage", *set_run_cost(run)]
    if error:
        run.error = error
        fields.append("error")
    if (
        status == AgentRunStatus.CANCELLED
        and run.cancellation_requested_at
        and not run.cancellation_confirmed_at
    ):
        run.cancellation_confirmed_at = timezone.now()
        fields.append("cancellation_confirmed_at")
    run.save(update_fields=fields)


def complete(run, hermes, detail, output):
    """Close a run Hermes confirmed as completed, with its usage and cost."""
    run.raw_output = output
    run.save(update_fields=["raw_output", "updated_at"])
    settle(
        run,
        AgentRunStatus.COMPLETED,
        detail,
        terminal=True,
        usage=hermes.fetch_usage(run.hermes_run_id),
    )
    sync_pending_learning(run.project)


def cancelled_before_start(run):
    """Honor a cancellation requested before the run reached Hermes."""
    run.refresh_from_db(fields=["cancellation_requested_at", "hermes_run_id"])
    if not run.cancellation_requested_at or run.hermes_run_id:
        return False
    # Not "confirmed": Hermes never saw this run, so there is nothing to confirm.
    run.status = AgentRunStatus.CANCELLED
    run.status_detail = "Cancelled before agent execution started."
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "status_detail", "completed_at", "updated_at"])
    return True


def start(run, hermes, prompt):
    """Write the profile files, record what the run is sent, and start it.

    Returns True once Hermes is running it. A runtime that cannot be reached
    is retried briefly before the run is failed.
    """
    if cancelled_before_start(run):
        return False
    files = profile_files(run.project)
    write_profile_files(run.project, files)
    run.prompt = prompt.record(files)
    run.save(update_fields=["prompt", "updated_at"])
    session_id = run.runtime_session_id
    if run.role == AgentRole.CHAT:
        # Hermes reuses a session's system prompt on every later turn, so a
        # thread moves to a new session when its files change; history is
        # sent with every turn either way.
        session_id = f"{session_id}-{files.fingerprint()}"

    for attempt in range(1, _START_ATTEMPTS + 1):
        try:
            started = hermes.start_run(
                input_text=prompt.input,
                instructions=prompt.instructions,
                session_id=session_id,
                session_key=profile_name_for(run.project),
                conversation_history=prompt.history,
            )
            break
        except requests.RequestException as exc:
            if attempt == _START_ATTEMPTS:
                logger.error("Run %s could not start: %s", run.id, exc)
                settle(
                    run,
                    AgentRunStatus.FAILED,
                    "Could not reach the agent runtime.",
                    terminal=True,
                    error=str(exc),
                )
                return False
            set_detail(run, "Waiting for the agent runtime…")
            time.sleep(2 * attempt)

    run.hermes_run_id = started["run_id"]
    run.save(update_fields=["hermes_run_id", "updated_at"])
    run.refresh_from_db(fields=["cancellation_requested_at"])
    if run.cancellation_requested_at:
        execute_pending_cancellation(run.id, hermes=hermes)
        return False
    return True


class RunStream:
    """A started run's events, for the browser or a worker.

    Iterating yields ``{"type": "delta" | "tool", ...}`` and records each tool
    call. Afterwards ``result()`` gives the final text once Hermes confirms the
    run completed, or None with the run's real state already saved.
    """

    def __init__(self, run, hermes, on_tool=None):
        self.run = run
        self.hermes = hermes
        self.on_tool = on_tool
        self._text = None

    def __iter__(self):
        run = self.run
        chunks, seen_ids, preview, terminal = [], set(), "", ""
        try:
            for event in self.hermes.stream_events(run.hermes_run_id):
                kind = event.get("event")
                tool = event.get("tool", "")
                if kind == "message.delta" and event.get("delta"):
                    chunks.append(event["delta"])
                    yield {"type": "delta", "delta": event["delta"]}
                elif kind == "tool.started":
                    preview = event.get("preview") or ""
                    yield {"type": "tool", "phase": "started", "tool": tool}
                elif kind == "tool.completed":
                    arguments = {"query": preview} if preview else {}
                    call = record_tool_call(run, event, arguments, seen_ids)
                    if self.on_tool:
                        self.on_tool(call)
                    ok = call.status != AgentToolCallStatus.ERROR
                    yield {"type": "tool", "phase": "completed", "tool": tool, "ok": ok}
                elif kind in _TERMINAL_EVENTS:
                    terminal = kind
                    break
        except requests.RequestException as exc:
            logger.warning("Run %s stream ended early: %s", run.id, exc)
            terminal = ""
        if terminal == "run.completed" and chunks:
            self._text = "".join(chunks)

    def result(self, wait_seconds=0):
        """The confirmed final text, polling up to ``wait_seconds`` if needed."""
        if self._text is not None:
            return self._text
        return await_result(self.run, self.hermes, wait_seconds)


def _poll(hermes, run):
    try:
        return hermes.get_run(run.hermes_run_id)
    except requests.RequestException as exc:
        logger.warning("Run %s: Hermes run record unavailable: %s", run.id, exc)
        return None


def await_result(run, hermes, wait_seconds):
    """The run's final text from Hermes's run record, polling while it works.

    Returns None with the run's real state saved when it did not complete;
    a run still working after ``wait_seconds`` stays live.
    """
    record = _poll(hermes, run)
    deadline = time.monotonic() + wait_seconds
    while (
        record is not None
        and str(record.get("status") or "").lower() in LIVE_RUN_STATUSES
        and time.monotonic() < deadline
    ):
        set_detail(run, "Still working…")
        time.sleep(_POLL_SECONDS)
        record = _poll(hermes, run) or record

    if record is None:
        settle(
            run,
            AgentRunStatus.OUTCOME_UNKNOWN,
            "Hermes outcome unconfirmed; terminal status is unavailable.",
        )
        return None
    status = str(record.get("status") or "").lower()
    if status == AgentRunStatus.COMPLETED:
        output = record.get("output")
        return output if isinstance(output, str) else ""
    if status == AgentRunStatus.FAILED:
        settle(
            run,
            AgentRunStatus.FAILED,
            "Hermes confirmed that the run failed.",
            terminal=True,
            # Upstream error text can carry provider details; keep it out.
            error="Hermes reported a failed run.",
        )
    elif status == AgentRunStatus.CANCELLED:
        settle(
            run,
            AgentRunStatus.CANCELLED,
            "Hermes confirmed that the run was cancelled.",
            terminal=True,
            usage=safe_hermes_usage(record.get("usage")),
        )
    elif status in LIVE_RUN_STATUSES:
        settle(run, status, f"Hermes still reports {status}; outcome pending.")
    else:
        settle(
            run,
            AgentRunStatus.OUTCOME_UNKNOWN,
            f"Hermes returned an unrecognized run status: {status or 'empty'}.",
        )
    return None


def notify_thread(run_id):
    """Tell the run's chat thread it ended, whichever path ended it."""
    run = ResearchAgentRun.objects.select_related("thread").filter(id=run_id).first()
    if run is not None and run.thread_id and run.status not in LIVE_RUN_STATUSES:
        on_run_finished(run)
