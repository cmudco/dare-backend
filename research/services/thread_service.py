"""
Chat threads as the hub of research mode.

A thread is one persistent conversation. From it the agent launches Scouts
(parallel Hermes runs, each with an isolated context), and back into it DARE
pushes what happens elsewhere — a Scout finishing, a review decision with the
user's note, a memory proposal — as event rows both sides can see. When
events arrive the agent has not reacted to, DARE runs a follow-up turn.
"""

import hashlib
import hmac
import logging
import re

import django_rq
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from research.constants import (
    LIVE_RUN_STATUSES,
    MAX_SCOUTS_PER_LAUNCH,
    AgentRunStatus,
    AgentToolCallStatus,
    ChatMessageRole,
    ResearchSessionMode,
    StagingItemStatus,
    ThreadEventType,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchChatMessage,
    ResearchSession,
    ResearchStagingItem,
    SoulFile,
)
from research.services.cancellation_service import request_run_cancellation

logger = logging.getLogger(__name__)

_TOKEN_SALT = "research.thread"
_TOKEN_SIG_CHARS = 10
_TOKEN_RE = re.compile(r"t(?P<id>\d+)-(?P<sig>[0-9a-f]{10})")
_RUN_TOKEN_RE = re.compile(r"r(?P<id>\d+)-(?P<sig>[0-9a-f]{10})")
CHAT_ROLE = "main-assistant"


class ThreadTokenInvalid(Exception):
    """A thread token was forged, truncated, or names a retired thread."""


# ── Identity ─────────────────────────────────────────────────────────────────


def _token_signature(thread_id, project_id, kind="thread"):
    message = f"{_TOKEN_SALT}:{kind}:{thread_id}:{project_id}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()[
        :_TOKEN_SIG_CHARS
    ]


def thread_token(thread):
    """An unforgeable handle the agent passes back to DARE's thread tools.

    The runtime may share one MCP connection across profiles, so the caller's
    connection identity cannot say whose thread a call is for; the token can,
    and only DARE can mint it. Kept short ("t48-3f9a2c1b0e") because a model
    copies it into every call — a long signed blob got garbled in transit.
    """
    return f"t{thread.id}-{_token_signature(thread.id, thread.project_id)}"


def resolve_thread_token(token):
    match = _TOKEN_RE.fullmatch(str(token or "").strip())
    if match is None:
        raise ThreadTokenInvalid("Unknown thread.")
    thread = (
        ResearchSession.active_objects.filter(
            id=int(match["id"]), mode=ResearchSessionMode.CHAT
        )
        .select_related("project", "user")
        .first()
    )
    if thread is None or not hmac.compare_digest(
        match["sig"], _token_signature(thread.id, thread.project_id)
    ):
        raise ThreadTokenInvalid("Unknown thread.")
    return thread


def run_token(run):
    """The handle a Scout passes to stage_finding — names exactly one run."""
    return f"r{run.id}-{_token_signature(run.id, run.project_id, 'run')}"


def resolve_run_token(token):
    """The live Scout run ``token`` names, or ThreadTokenInvalid."""
    match = _RUN_TOKEN_RE.fullmatch(str(token or "").strip())
    if match is None:
        raise ThreadTokenInvalid("Unknown run.")
    run = (
        ResearchAgentRun.active_objects.filter(id=int(match["id"]), role="scout")
        .select_related("project")
        .first()
    )
    if run is None or not hmac.compare_digest(
        match["sig"], _token_signature(run.id, run.project_id, "run")
    ):
        raise ThreadTokenInvalid("Unknown run.")
    if run.status not in LIVE_RUN_STATUSES:
        raise ThreadTokenInvalid("This Scout run has already finished.")
    return run


# ── Events ───────────────────────────────────────────────────────────────────


def post_event(thread, event_type, content, payload):
    return ResearchChatMessage.objects.create(
        session=thread,
        project_id=thread.project_id,
        user_id=thread.user_id,
        role=ChatMessageRole.EVENT,
        event_type=event_type,
        content=content,
        payload=payload,
    )


def _soul_label(project):
    soul = SoulFile.active_objects.filter(project=project).first()
    version = soul.current_version() if soul else None
    return f"v{version.version}" if version else ""


# ── Scouts ───────────────────────────────────────────────────────────────────


def _enqueue(path, *args):
    django_rq.get_queue("default").enqueue(path, *args)


def launch_scouts(thread, scouts):
    """Start one Scout run per entry in ``scouts`` and record the launch.

    Each entry is ``{"focus": str, "task": str, "depth": "quick"|"deep"}``,
    already validated by the caller. Returns the created runs.
    """
    project = thread.project
    if len(scouts) > MAX_SCOUTS_PER_LAUNCH:
        raise ValueError(f"At most {MAX_SCOUTS_PER_LAUNCH} Scouts per launch.")
    scout_session = ResearchSession._get_or_create(
        project, thread.user, ResearchSessionMode.SCOUT
    )
    soul_label = _soul_label(project)
    tools = list(project.enabled_tools)
    now = timezone.now()
    with transaction.atomic():
        runs = [
            ResearchAgentRun.objects.create(
                session=scout_session,
                thread=thread,
                project=project,
                user=thread.user,
                role="scout",
                mode=ResearchSessionMode.SCOUT,
                task=scout["task"],
                status=AgentRunStatus.RUNNING,
                status_detail="Queued…",
                soul_file_version=soul_label,
                allowed_tools=tools or ["web"],
                selected_context={
                    "depth": scout["depth"],
                    "tools": tools,
                    "focus": scout["focus"],
                },
                started_at=now,
            )
            for scout in scouts
        ]
        post_event(
            thread,
            ThreadEventType.SCOUTS_LAUNCHED,
            ", ".join(s["focus"] for s in scouts),
            {
                "scouts": [
                    {"runId": run.id, "focus": scout["focus"], "task": scout["task"]}
                    for run, scout in zip(runs, scouts)
                ]
            },
        )
        transaction.on_commit(
            lambda: [_enqueue("research.tasks.run_scout_job", r.id) for r in runs]
        )
    return runs


def redirect_scout(run, instruction):
    """Point ``run`` at ``instruction``; returns the run that now carries it.

    A Scout that is mid-turn is steered in place, so it keeps what it has
    already read. One that is not running is replaced by a fresh Scout.
    """
    if run.status in LIVE_RUN_STATUSES and run.hermes_run_id:
        from research.services.hermes_service import get_hermes_service

        hermes = get_hermes_service(run.project)
        if hermes.steer_run(
            run.hermes_run_id, f"New direction from the user: {instruction}"
        ):
            context = dict(run.selected_context or {})
            context["redirects"] = [*context.get("redirects", []), instruction]
            run.selected_context = context
            run.status_detail = f"Redirected: {instruction[:120]}"
            run.save(update_fields=["selected_context", "status_detail", "updated_at"])
            return run
        request_run_cancellation(run.id, run.user)
    focus = (run.selected_context or {}).get("focus") or "Scout"
    task = f"{run.task}\n\nThe user redirected this search: {instruction}"
    depth = (run.selected_context or {}).get("depth") or "deep"
    return launch_scouts(
        run.thread, [{"focus": f"{focus} (redirected)", "task": task, "depth": depth}]
    )[0]


def scout_summary(run):
    items = ResearchStagingItem.active_objects.filter(run=run)
    learning = run.learning or {}
    return {
        "runId": run.id,
        "focus": (run.selected_context or {}).get("focus") or run.task[:60],
        "status": run.status,
        "statusDetail": run.status_detail,
        "staged": items.count(),
        "cost": float(run.cost or 0),
        "titles": list(items.values_list("title", flat=True)[:12]),
        "applied": learning.get("applied") or [],
        "setAside": learning.get("setAside") or [],
        "activity": scout_activity(run),
    }


_TOOL_LABELS = {
    "web_search": "Searched",
    "search_papers": "Searched papers",
    "fetch_page": "Read",
}


def scout_activity(run, limit=8):
    """What the Scout has done so far, newest last: each search and page read.

    Rows are written as each tool call completes, so polling this is how the
    user watches a Scout work rather than a spinner.
    """
    calls = list(
        ResearchAgentToolCall.active_objects.filter(run=run).order_by("-created_at")[
            :limit
        ]
    )
    calls.reverse()
    rows = []
    for call in calls:
        base = call.tool.rsplit("__", 1)[-1].replace("mcp_dare_", "")
        arguments = call.arguments or {}
        rows.append(
            {
                "verb": _TOOL_LABELS.get(base, "Used"),
                "tool": base,
                "detail": str(arguments.get("url") or arguments.get("query") or "")[
                    :200
                ],
                "ok": call.status != AgentToolCallStatus.ERROR,
                "error": call.error[:160],
                "at": call.created_at.isoformat(),
            }
        )
    return rows


# ── Follow-up turns ──────────────────────────────────────────────────────────


def thread_has_active_turn(thread):
    return ResearchAgentRun.active_objects.filter(
        thread=thread, role=CHAT_ROLE, status__in=LIVE_RUN_STATUSES
    ).exists()


def request_followup(thread):
    """Have the agent react to new thread events — now, or after the live turn."""
    if thread_has_active_turn(thread):
        if not thread.followup_pending:
            thread.followup_pending = True
            thread.save(update_fields=["followup_pending", "updated_at"])
        return False
    _enqueue("research.tasks.run_thread_followup_job", thread.id)
    return True


def on_run_finished(run):
    """Bookkeeping when any thread-linked run reaches a terminal state."""
    thread = run.thread
    if thread is None:
        return
    if run.role == "scout":
        summary = scout_summary(run)
        post_event(
            thread,
            ThreadEventType.SCOUT_FINISHED,
            f"{summary['focus']}: {run.status_detail or run.status}",
            summary,
        )
        siblings_live = (
            ResearchAgentRun.active_objects.filter(
                thread=thread, role="scout", status__in=LIVE_RUN_STATUSES
            )
            .exclude(id=run.id)
            .exists()
        )
        if not siblings_live:
            request_followup(thread)
    elif run.role == CHAT_ROLE and thread.followup_pending:
        thread.followup_pending = False
        thread.save(update_fields=["followup_pending", "updated_at"])
        _enqueue("research.tasks.run_thread_followup_job", thread.id)


def record_review_event(item, decision, note, tags):
    """Push a review decision on a thread-launched finding back into its thread."""
    run = item.run
    thread = run.thread if run else None
    if thread is None:
        return None
    event = post_event(
        thread,
        ThreadEventType.REVIEW_DECISION,
        item.title,
        {
            "itemId": item.id,
            "title": item.title,
            "decision": decision,
            "note": note,
            "tags": tags,
            "runId": run.id,
        },
    )
    still_waiting = ResearchStagingItem.active_objects.filter(
        run__thread=thread, status=StagingItemStatus.STAGED
    ).exists()
    if not still_waiting and _has_review_notes(unseen_events(thread)):
        # One reply per review session: after the last finding from this
        # thread is decided, covering every note left along the way.
        request_followup(thread)
    return event


def _has_review_notes(events):
    return any(
        e.event_type == ThreadEventType.REVIEW_DECISION
        and ((e.payload or {}).get("note") or (e.payload or {}).get("tags"))
        for e in events
    )


# ── What the agent sees ──────────────────────────────────────────────────────


def _event_line(message):
    payload = message.payload or {}
    kind = message.event_type
    if kind == ThreadEventType.SCOUTS_LAUNCHED:
        return f"[Scouts launched] {message.content}"
    if kind == ThreadEventType.SCOUT_FINISHED:
        line = f"[Scout finished] {message.content}"
        if payload.get("titles"):
            line += "\n  Staged: " + "; ".join(payload["titles"][:8])
        for row in (payload.get("applied") or [])[:5]:
            line += (
                f"\n  Applied {row.get('memory', '')}: {row.get('effect', '')[:240]}"
            )
        for row in (payload.get("setAside") or [])[:8]:
            line += f"\n  Set aside “{row.get('title', '')}”: {row.get('reason', '')}"
        return line
    if kind == ThreadEventType.REVIEW_DECISION:
        verdict = {"approve": "approved", "reject": "rejected", "later": "deferred"}
        line = (
            f"[Review] The user {verdict.get(payload.get('decision'), 'reviewed')} "
            f"finding #{payload.get('itemId')} “{payload.get('title', '')}”"
        )
        if payload.get("tags"):
            line += f" (reasons: {', '.join(payload['tags'])})"
        if payload.get("note"):
            line += f". Their note: “{payload['note']}”"
        return line
    if kind == ThreadEventType.MEMORY_PROPOSED:
        return f"[Memory proposed, awaiting the user] {message.content}"
    if kind == ThreadEventType.MEMORY_DECIDED:
        return (
            f"[Memory {payload.get('status', 'decided')} by the user"
            f" as {payload.get('layer', '')}] {message.content}"
        )
    return f"[{kind}] {message.content}"


def conversation_history(thread, before=None, max_messages=60, max_chars=60000):
    """The thread as structured turns for the runtime, oldest first.

    Events ride as user-role notes from DARE so the model reads them as facts
    about the workspace, not as things it said. The runtime compresses long
    histories itself, so this only caps the extreme case.
    """
    messages = ResearchChatMessage.active_objects.filter(session=thread)
    if before is not None:
        messages = messages.filter(created_at__lt=before)
    rows = list(messages.order_by("-created_at")[:max_messages])
    rows.reverse()
    history = []
    for message in rows:
        if message.role == ChatMessageRole.EVENT:
            content = f"(DARE workspace event) {_event_line(message)}"
            role = "user"
        else:
            content, role = message.content, message.role
        if not content:
            continue
        if history and history[-1]["role"] == role == "user":
            history[-1]["content"] += "\n" + content
        else:
            history.append({"role": role, "content": content})
    while sum(len(h["content"]) for h in history) > max_chars and len(history) > 2:
        history.pop(0)
    return history


def _findings_block(run_id, per_item_chars=600):
    """What a finished Scout found, with the facts, so chat can answer from it."""
    items = ResearchStagingItem.active_objects.filter(run_id=run_id).order_by("id")
    lines = []
    for item in items:
        kind = (item.provenance or {}).get("kind") or "source"
        meta = ", ".join(str(v) for v in (item.year, kind) if v)
        facts = " ".join((item.rationale or "").split())[:per_item_chars]
        lines.append(f"  #{item.id} {item.title} ({meta}) {item.url}\n    {facts}")
    return "\n".join(lines)


def followup_input(thread, preamble):
    """The follow-up prompt: unanswered events, plus what finished Scouts found."""
    events = unseen_events(thread)
    lines = "\n".join(f"- {_event_line(e)}" for e in events)
    findings = [
        _findings_block((e.payload or {}).get("runId"))
        for e in events
        if e.event_type == ThreadEventType.SCOUT_FINISHED
        and (e.payload or {}).get("runId")
    ]
    text = f"{preamble}\n\nNEW SINCE YOUR LAST REPLY:\n{lines}"
    findings = [block for block in findings if block]
    if findings:
        text += "\n\nWHAT THE SCOUTS FOUND (your evidence):\n" + "\n".join(findings)
    return text


def unseen_events(thread):
    """Events since the agent last spoke in this thread."""
    last_reply = (
        ResearchChatMessage.active_objects.filter(
            session=thread, role=ChatMessageRole.ASSISTANT
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    events = ResearchChatMessage.active_objects.filter(
        session=thread, role=ChatMessageRole.EVENT
    )
    if last_reply:
        events = events.filter(created_at__gt=last_reply)
    return list(events.order_by("created_at"))


def thread_digest(thread, max_chars=4000):
    """A compact brief of the thread for a Scout's isolated context."""
    lines = []
    for turn in conversation_history(thread, max_messages=20):
        who = "User" if turn["role"] == "user" else "Research chat"
        lines.append(f"{who}: {turn['content']}")
    return "\n".join(lines)[-max_chars:]
