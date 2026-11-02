"""
One agent turn in a chat thread.

Streamed live to the browser when the user sends a message, and drained in a
worker for a follow-up turn DARE starts when thread events (finished Scouts,
review notes) arrive. Both persist the same records.
"""

import django_rq
from django.conf import settings
from django.utils import timezone

from research.constants import (
    LIVE_RUN_STATUSES,
    AgentRole,
    AgentRunStatus,
    ChatMessageRole,
    ResearchSessionMode,
)
from research.models import (
    ResearchAgentRun,
    ResearchChatMessage,
    ResearchSession,
    SoulFile,
)
from research.services.profile_service import hermes_for
from research.services.prompt_builder import chat_prompt
from research.services.run_service import RunStream, await_result, complete, start
from research.services.thread_service import (
    on_run_finished,
    thread_has_active_turn,
    unseen_events,
)

_ERRORS = {
    AgentRunStatus.FAILED: "The agent run failed.",
    AgentRunStatus.CANCELLED: "The agent run was cancelled.",
}


class TurnStartFailed(Exception):
    """The runtime could not start the turn; the run is already marked failed."""


def start_turn(thread, user, text="", *, scout_now=False):
    """Record the user's message (none for a follow-up) and start the turn.

    Returns ``(run, hermes)``; raises TurnStartFailed when the runtime could
    not be reached.
    """
    project = thread.project
    message = None
    if text and not thread.title:
        thread.title = text[:80]
        thread.save(update_fields=["title", "updated_at"])
    if text:
        message = ResearchChatMessage.objects.create(
            session=thread,
            project=project,
            user=user,
            role=ChatMessageRole.USER,
            content=text,
        )
    prompt = chat_prompt(thread, message, scout_now=scout_now)
    run = ResearchAgentRun.objects.create(
        session=thread,
        thread=thread,
        project=project,
        user=user,
        role=AgentRole.CHAT,
        mode=ResearchSessionMode.CHAT,
        task=text or "Follow-up on thread events",
        status=AgentRunStatus.RUNNING,
        soul_file_version=SoulFile.current_label(project),
        selected_context={"followup": message is None, "scoutNow": scout_now},
        started_at=timezone.now(),
    )
    hermes = hermes_for(project)
    if not start(run, hermes, prompt):
        raise TurnStartFailed(run.error or run.status_detail)
    return run, hermes


def stream_turn(run, hermes):
    """Yield browser-facing events for the turn, then save the reply.

    When the stream goes quiet while Hermes is still working (a slow or
    retried model call), a worker waits for the reply instead and the thread
    shows it once it lands.
    """
    stream = RunStream(run, hermes)
    yield from stream
    text = stream.result()
    if text is None and run.status in LIVE_RUN_STATUSES:
        django_rq.get_queue("default").enqueue("research.tasks.finish_turn_job", run.id)
        yield {"type": "pending", "runId": run.id}
        return
    if text is None:
        on_run_finished(run)
        yield {
            "type": "error",
            "error": _ERRORS.get(
                run.status, "The agent outcome could not be confirmed."
            ),
        }
        return
    message = _save_reply(run, hermes, text)
    yield {"type": "done", "messageId": message.id, "runId": run.id}


def _save_reply(run, hermes, text):
    thread = run.thread
    message = ResearchChatMessage.objects.create(
        session=thread,
        project=run.project,
        user=run.user,
        role=ChatMessageRole.ASSISTANT,
        content=text.strip(),
        run=run,
    )
    complete(run, hermes, "Replied.", text)
    thread.last_run_at = timezone.now()
    thread.save(update_fields=["last_run_at", "updated_at"])
    on_run_finished(run)
    return message


def finish_turn(run_id):
    """Wait for a turn whose stream ended while Hermes was still working."""
    run = (
        ResearchAgentRun.objects.select_related("thread", "project")
        .filter(id=run_id, role=AgentRole.CHAT, status__in=LIVE_RUN_STATUSES)
        .first()
    )
    if run is None:
        return
    hermes = hermes_for(run.project)
    text = await_result(run, hermes, settings.HERMES_RUN_WAIT_SECONDS)
    if text is None:
        on_run_finished(run)
        return
    _save_reply(run, hermes, text)


def run_followup(thread_id):
    """Let the agent react to thread events nobody has answered yet.

    Safe to run more than once: it does nothing while a turn is live (that
    turn's end asks again) or when every event has been answered.
    """
    thread = (
        ResearchSession.active_objects.filter(id=thread_id)
        .select_related("project", "user")
        .first()
    )
    if thread is None or thread_has_active_turn(thread) or not unseen_events(thread):
        return
    try:
        run, hermes = start_turn(thread, thread.user)
    except TurnStartFailed:
        return
    for _ in stream_turn(run, hermes):
        pass
