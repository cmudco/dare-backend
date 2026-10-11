"""
One agent turn in a chat thread, run on Hermes.

Used two ways: streamed live to the browser when the scholar sends a message,
and drained in a worker for a follow-up turn DARE starts when thread events
(finished Scouts, review notes) arrive. Both persist the same records.
"""

import logging

from django.utils import timezone

from research.constants import (
    MAX_SCOUTS_PER_LAUNCH,
    AgentRunStatus,
    AgentToolCallStatus,
    ChatMessageRole,
    ResearchSessionMode,
)
from research.models import ResearchAgentRun, ResearchChatMessage, SoulFile
from research.services.audit_service import record_tool_call
from research.services.hermes_service import get_hermes_service, safe_hermes_usage
from research.services.learning_service import sync_pending_learning
from research.services.thread_service import (
    CHAT_ROLE,
    conversation_history,
    on_run_finished,
    thread_token,
    unseen_events,
)

logger = logging.getLogger(__name__)


class TurnStartFailed(Exception):
    """The runtime could not start the turn; the run is already marked failed."""


HUB_BRIEF = """You are the research chat for this project: the scholar's thinking partner and the place research work is planned, launched and discussed. Work under the standards above.

HOW A SESSION GOES
1. When the scholar brings a question, ask at most ONE clarifying question if the scope is genuinely ambiguous. Otherwise propose a short plan: which Scouts to run, each with a one-line focus (e.g. "3 Scouts: outcomes, bias, implementation").
2. Let the scholar edit the plan. Launch when they agree — or straight away if they asked you to just go.
3. Launch with mcp__dare__launch_scouts. Scouts run in parallel in the background, each with its own isolated context; write each task so it stands alone (scope, population, what counts as evidence, what to exclude). Do not wait for them and do not poll thread_status — tell the scholar they are running and end your reply; DARE tells you when they finish.
4. Findings go to the scholar's Review Inbox, never into your memory. When Scouts finish, DARE tells you in the thread; summarise what they staged and what is still open.
5. Follow-ups like "go deeper on #3" or "drop vendor sources" become new, sharper Scouts.

THREAD TOOLS (pass thread="%(token)s" to every one)
- mcp__dare__launch_scouts: start up to %(max_scouts)d Scouts: scouts=[{focus, task, depth: "quick"|"deep"}].
- mcp__dare__thread_status: the live state of this thread's Scouts and their findings.
- mcp__dare__search_knowledge: search the findings the scholar APPROVED from the Review Inbox. That is separate from your project memory and standards, which are already loaded above — an empty result means "no approved findings yet", never "the project is empty".
- mcp__dare__web_search and mcp__dare__fetch_page for your own quick look-ups. Never use a runtime-native web, extract or browser tool.

WORKSPACE EVENTS
Lines marked "(DARE workspace event)" are facts DARE recorded: Scouts launching and finishing, the scholar approving or rejecting a finding with a note, memory proposals and decisions. They are not the scholar speaking, but a review note IS the scholar's voice — take it seriously.

LEARNING — EVERY CHANGE NEEDS THE SCHOLAR'S APPROVAL
You have a `memory` tool and a `skill_manage` tool. Every write you make is staged for the scholar to approve; nothing is saved until they do. Say "I've proposed remembering …", never "I'll remember …".
- target "user" = researcher-wide: how this scholar works in ANY project — method, standards, sources they trust or distrust, citation style, how they want answers. Example: "Prefers peer-reviewed sources; set preprints aside and say so."
- target "memory" = this project only: its scope, population, framing, decisions. Example: "Scope: emergency departments only, not radiology."
- When a review note or a correction reveals a durable preference, propose ONE entry for it, phrased as a rule you will follow. Do not propose transient chatter or research findings.
- When the scholar has corrected the same kind of thing about three times, or walks you through a method they want reused, propose a skill with skill_manage (action "create", a short lowercase-hyphenated name, a SKILL.md with frontmatter `name` and `description` and numbered steps).
- Your approved memory and skills are already loaded. Apply them, and when one changes what you do, say so in a few words ("set aside 2 preprints, per your note").

Be honest about tool failures: say plainly what failed and why; do not retry a tool that returned a quota or auth error."""


def _soul(project):
    soul = SoulFile.active_objects.filter(project=project).first()
    version = soul.current_version() if soul else None
    return (version.content if version else ""), (
        f"v{version.version}" if version else ""
    )


def build_hub_instructions(thread, soul_content):
    project = thread.project
    parts = [soul_content] if soul_content else []
    parts.append(
        HUB_BRIEF % {"token": thread_token(thread), "max_scouts": MAX_SCOUTS_PER_LAUNCH}
    )
    if project.question and project.question.strip():
        parts.append(
            "# Project\nResearch question: "
            + project.question.strip()
            + (f"\nField: {project.field}" if project.field else "")
        )
    else:
        parts.append(
            "# Project\nNo research question is set yet. If the scholar's message "
            "implies one, help them sharpen it."
        )
    return "\n\n".join(parts)


FOLLOWUP_INPUT = (
    "New workspace events arrived since your last reply (listed in the thread "
    "above). Respond to the scholar briefly and concretely:\n"
    "- If Scouts finished, summarise what they staged, what they set aside "
    "because of the scholar's memory, and what is still open.\n"
    "- Only if the events include review notes from the scholar, acknowledge "
    "them in a sentence and say what you will do differently; if a note reveals "
    "a durable preference, propose ONE memory entry for it now.\n"
    "- State only what the events say. Never guess why something was set aside "
    "or what a study found.\n"
    "Do not launch new Scouts unless the scholar asked for them."
)


SCOUT_NOW = (
    "The scholar sent this in Scout-now mode: if it is a research request, "
    "launch exactly ONE Scout for it immediately with mcp__dare__launch_scouts "
    "— no plan, no questions — writing a self-contained task from their words "
    "and the project context (depth deep unless they ask for quick), then say "
    "in one line what it will look for. If it is not a research request (a "
    "greeting, a question about the project, small talk), just reply and "
    "launch nothing."
)


def start_turn(thread, user, text, *, followup=False, scout_now=False):
    """Create the run, record the scholar's message, and start the Hermes run.

    Returns ``(run, hermes)``. Raises TurnStartFailed after marking the run
    failed when the runtime cannot be reached.
    """
    project = thread.project
    soul_content, soul_label = _soul(project)
    user_message = None
    if not followup:
        user_message = ResearchChatMessage.objects.create(
            session=thread,
            project=project,
            user=user,
            role=ChatMessageRole.USER,
            content=text,
        )
    history = conversation_history(
        thread, before=user_message.created_at if user_message else None
    )
    run = ResearchAgentRun.objects.create(
        session=thread,
        thread=thread,
        project=project,
        user=user,
        role=CHAT_ROLE,
        mode=ResearchSessionMode.CHAT,
        task="Follow-up on thread events" if followup else text,
        status=AgentRunStatus.RUNNING,
        soul_file_version=soul_label,
        selected_context={
            "followup": followup,
            "scoutNow": scout_now,
            "historyTurns": len(history),
        },
        started_at=timezone.now(),
    )
    hermes = get_hermes_service(project)
    hermes.provision_soul(soul_content)
    try:
        started = hermes.start_run(
            input_text=FOLLOWUP_INPUT if followup else text,
            instructions=build_hub_instructions(thread, soul_content)
            + (f"\n\n{SCOUT_NOW}" if scout_now else ""),
            session_id=thread.hermes_session_id,
            session_key=f"dare-proj{project.id}",
            conversation_history=history,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed run
        logger.error("research.chat run %s start failed: %s", run.id, exc)
        run.status = AgentRunStatus.FAILED
        run.error = str(exc)
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error", "completed_at", "updated_at"])
        raise TurnStartFailed(str(exc)) from exc
    run.hermes_run_id = started["run_id"]
    run.save(update_fields=["hermes_run_id", "updated_at"])
    logger.info(
        "research.chat run %s started: thread=%s followup=%s history=%s hermes_run=%s",
        run.id,
        thread.id,
        followup,
        len(history),
        run.hermes_run_id,
    )
    return run, hermes


def _settle(run, status, detail, *, terminal, usage=None, error=""):
    run.status = status
    run.status_detail = detail
    run.completed_at = timezone.now() if terminal else None
    fields = ["status", "status_detail", "completed_at", "updated_at"]
    if usage:
        run.usage = usage
        fields.append("usage")
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


def _verify(run, hermes):
    """Resolve an ambiguous stream end from Hermes's pollable record.

    Returns the final text on confirmed completion, else ``None`` with the
    run's honest state persisted and a user-facing error message.
    """
    try:
        terminal = hermes.get_run(run.hermes_run_id)
    except Exception:  # noqa: BLE001 - unconfirmed is an honest state
        _settle(
            run,
            AgentRunStatus.OUTCOME_UNKNOWN,
            "Hermes outcome unconfirmed; terminal status is unavailable.",
            terminal=False,
        )
        return None, "The agent outcome could not be confirmed."
    status = str(terminal.get("status") or "").lower()
    if status == AgentRunStatus.COMPLETED:
        output = terminal.get("output")
        return (output if isinstance(output, str) else ""), ""
    if status == AgentRunStatus.FAILED:
        _settle(
            run,
            AgentRunStatus.FAILED,
            "Hermes confirmed that the chat run failed.",
            terminal=True,
            error="Hermes reported a failed run.",
        )
        return None, "The agent run failed."
    if status == AgentRunStatus.CANCELLED:
        _settle(
            run,
            AgentRunStatus.CANCELLED,
            "Hermes confirmed that the chat run was cancelled.",
            terminal=True,
            usage=safe_hermes_usage(terminal.get("usage")),
        )
        return None, "The agent run was cancelled."
    if status in {
        AgentRunStatus.STARTED,
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_FOR_APPROVAL,
        AgentRunStatus.STOPPING,
    }:
        _settle(
            run,
            status,
            f"Hermes still reports {status}; terminal outcome is pending.",
            terminal=False,
        )
        return None, "The agent run is still active."
    _settle(
        run,
        AgentRunStatus.OUTCOME_UNKNOWN,
        "Hermes returned an unrecognized terminal status.",
        terminal=False,
    )
    return None, "The agent outcome could not be confirmed."


def stream_turn(run, hermes):
    """Consume the run's events, yielding browser-facing dicts; persist the end."""
    chunks = []
    seen_ids = set()
    terminal_event = ""
    stream_error = None
    try:
        for event in hermes.stream_events(run.hermes_run_id):
            kind = event.get("event")
            if kind == "message.delta":
                delta = event.get("delta", "")
                if delta:
                    chunks.append(delta)
                    yield {"type": "delta", "delta": delta}
            elif kind == "tool.started":
                yield {
                    "type": "tool",
                    "phase": "started",
                    "tool": event.get("tool", ""),
                }
            elif kind == "tool.completed":
                call = record_tool_call(run, event, {}, seen_ids)
                yield {
                    "type": "tool",
                    "phase": "completed",
                    "tool": event.get("tool", ""),
                    "ok": call.status != AgentToolCallStatus.ERROR,
                }
            elif kind in {"run.completed", "run.failed", "run.cancelled"}:
                terminal_event = kind
                break
    except Exception as exc:  # noqa: BLE001 - verified below instead of guessed
        stream_error = exc
        logger.error("research.chat run %s stream failed: %s", run.id, exc)

    text = "".join(chunks)
    if stream_error or terminal_event != "run.completed" or not chunks:
        text, error = _verify(run, hermes)
        if text is None:
            on_run_finished(run)
            yield {"type": "error", "error": error}
            return

    thread = run.thread
    message = ResearchChatMessage.objects.create(
        session=thread,
        project=run.project,
        user=run.user,
        role=ChatMessageRole.ASSISTANT,
        content=text,
        run=run,
    )
    _settle(
        run,
        AgentRunStatus.COMPLETED,
        "Replied.",
        terminal=True,
        usage=hermes.fetch_usage(run.hermes_run_id),
    )
    thread.last_run_at = timezone.now()
    thread.save(update_fields=["last_run_at", "updated_at"])
    sync_pending_learning(run.project)
    on_run_finished(run)
    yield {"type": "done", "messageId": message.id, "runId": run.id}


def has_unseen_events(thread):
    return bool(unseen_events(thread))
