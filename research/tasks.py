"""
Background jobs for the Research app.

Delegated runs are long (search + reading + synthesis), so they run on
django-rq rather than tying up a request. Every job follows the same shape:
prepare (soul + input) → start the Hermes run → stream its events → parse the
structured reply → persist results. Hermes owns execution limits; the frontend
polls the run for live status and only DARE writes to the database.
"""

import logging
import time

from django.utils import timezone
from django_rq import job

from mcp.services.mcp_gateway import gather_tool_results
from research.constants import (
    LIVE_RUN_STATUSES,
    AgentRunStatus,
    AgentToolCallStatus,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchArtifact,
    ResearchKnowledgeItem,
    ResearchSession,
    ResearchStagingItem,
    SoulFile,
)
from research.services import (
    build_artifact_instructions,
    build_critic_instructions,
    build_scout_instructions,
    critic_input,
    get_hermes_service,
    parse_artifacts,
    parse_critic_verdict,
    parse_scout_learning,
    parse_staging_items,
    safe_hermes_usage,
)
from research.services.audit_service import record_tool_call, token_count
from research.services.chat_service import TurnStartFailed, start_turn, stream_turn
from research.services.learning_service import sync_pending_learning
from research.services.staging_service import merge_learning, stage_candidate
from research.services.thread_service import (
    on_run_finished,
    run_token,
    thread_digest,
    thread_has_active_turn,
    unseen_events,
)

logger = logging.getLogger(__name__)

# ── Shared run plumbing ──────────────────────────────────────────────────────


def _set_status(run, detail):
    run.status_detail = detail[:255]
    run.save(update_fields=["status_detail", "updated_at"])


def _fail(run, detail, exc):
    logger.error("Run %s failed: %s", run.id, exc)
    run.status = AgentRunStatus.FAILED
    run.error = str(exc)
    run.status_detail = detail
    run.completed_at = timezone.now()
    run.save(
        update_fields=["status", "error", "status_detail", "completed_at", "updated_at"]
    )


def _finish(run, detail, hermes, failed=False, raw_output=None):
    """Close out a run: final status + detail + token usage, in one save.

    ``raw_output`` is the agent's exact final response text; persisted verbatim
    for the run-details audit view (observability only, never re-parsed).
    """
    run.status = AgentRunStatus.FAILED if failed else AgentRunStatus.COMPLETED
    run.status_detail = detail[:255]
    run.completed_at = timezone.now()
    run.usage = hermes.fetch_usage(run.hermes_run_id)
    fields = ["status", "status_detail", "completed_at", "usage", "updated_at"]
    if raw_output is not None:
        run.raw_output = raw_output
        fields.append("raw_output")
    run.save(update_fields=fields)
    sync_pending_learning(run.project)


def _notify_thread(run_id):
    """Tell the run's chat thread it ended, whatever path ended it."""
    run = ResearchAgentRun.objects.select_related("thread").filter(id=run_id).first()
    if run is None or run.thread_id is None:
        return
    if run.status in LIVE_RUN_STATUSES:
        return
    on_run_finished(run)


def _save_verified_status(run, status, detail, *, terminal=False, usage=None):
    """Persist a Hermes-reported or explicitly unconfirmed execution state."""
    run.status = status
    run.status_detail = detail[:255]
    fields = ["status", "status_detail", "updated_at"]
    if terminal:
        run.completed_at = timezone.now()
        fields.append("completed_at")
    if isinstance(usage, dict):
        run.usage = usage
        fields.append("usage")
    run.save(update_fields=fields)


def _cancel_before_hermes_start(run):
    """Honor durable intent before a queued DARE job creates a Hermes run."""
    run.refresh_from_db(fields=["cancellation_requested_at", "hermes_run_id"])
    if not run.cancellation_requested_at or run.hermes_run_id:
        return False
    run.status = AgentRunStatus.CANCELLED
    run.status_detail = "Cancelled before agent execution started."
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "status_detail", "completed_at", "updated_at"])
    return True


def _verify_terminal_run(hermes, run, interrupted_detail):
    """Resolve ambiguous SSE termination through Hermes's pollable run state.

    Returns terminal output only for a confirmed completion. Every other result
    is persisted without fabricating a failure and returns None.
    """
    try:
        terminal = hermes.get_run(run.hermes_run_id)
    except Exception as exc:  # noqa: BLE001 - absence is itself an honest state
        logger.warning(
            "Could not verify terminal Hermes state for run %s: %s",
            run.hermes_run_id,
            exc,
        )
        _save_verified_status(
            run,
            AgentRunStatus.OUTCOME_UNKNOWN,
            "Hermes outcome unconfirmed; terminal status is unavailable.",
        )
        return None

    status = str(terminal.get("status") or "").lower()
    if status == AgentRunStatus.COMPLETED:
        output = terminal.get("output")
        return output if isinstance(output, str) else ""
    if status == AgentRunStatus.FAILED:
        error = terminal.get("error") or "Hermes reported a failed run."
        _fail(run, interrupted_detail, Exception(error))
        return None
    if status == AgentRunStatus.CANCELLED:
        _save_verified_status(
            run,
            AgentRunStatus.CANCELLED,
            "Hermes confirmed that the run was cancelled.",
            terminal=True,
            usage=safe_hermes_usage(terminal.get("usage")),
        )
        if getattr(run, "cancellation_requested_at", None) and not getattr(
            run, "cancellation_confirmed_at", None
        ):
            run.cancellation_confirmed_at = timezone.now()
            run.save(update_fields=["cancellation_confirmed_at", "updated_at"])
        return None
    if status in {
        AgentRunStatus.STARTED,
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_FOR_APPROVAL,
        AgentRunStatus.STOPPING,
    }:
        _save_verified_status(
            run,
            status,
            f"Hermes still reports {status}; terminal outcome is pending.",
        )
        return None

    _save_verified_status(
        run,
        AgentRunStatus.OUTCOME_UNKNOWN,
        f"Hermes returned an unrecognized run status: {status or 'empty'}.",
    )
    return None


def _project_soul(project):
    """The project's soul file, current version, and that version's content."""
    soul = SoulFile.active_objects.filter(project=project).first()
    version = soul.current_version() if soul else None
    return soul, version, (version.content if version else "")


def _knowledge_block(project, per_item_chars=300, max_items=12):
    """
    The scholar's approved durable knowledge, compact, for injection into a
    delegated run — so Scout builds on (not re-finds) the approved record and
    the Presenter grounds artifacts in it.
    """
    items = ResearchKnowledgeItem.active_objects.filter(project=project).select_related(
        "source_staging_item"
    )[:max_items]
    lines = []
    for k in items:
        src = k.source_staging_item
        cite = f"{src.title} ({src.authors}, {src.year})" if src else "Scholar note"
        body = (k.content or k.rationale or "").strip()[:per_item_chars]
        lines.append(f"- {cite}: {body}")
    return "\n".join(lines)


def _start_run(hermes, run, input_text, instructions):
    """
    Start the Hermes run and record its id. Each delegated run gets a fresh
    session (sharing one made every run replay the mode's whole history on
    every loop turn — the main token inflater; cross-run recall survives via
    Hermes's session summaries), while the session KEY pins long-term memory
    to the project. Returns the Hermes run id, or None after failing the run.
    """
    if _cancel_before_hermes_start(run):
        return None

    session_id = f"{run.session.hermes_session_id}-r{run.id}"
    session_key = f"dare-proj{run.project_id}"
    # The runtime can be briefly unreachable (a gateway restart/drain); a single
    # refused connection shouldn't sink the run, so retry with backoff before
    # giving up. Starting a run is idempotent here — nothing began if it raised.
    last_exc = None
    for attempt in range(3):
        try:
            started = hermes.start_run(
                input_text=input_text,
                instructions=instructions,
                session_id=session_id,
                session_key=session_key,
            )
            run.hermes_run_id = started["run_id"]
            run.save(update_fields=["hermes_run_id", "updated_at"])
            run.refresh_from_db(fields=["cancellation_requested_at"])
            if run.cancellation_requested_at:
                from research.services.cancellation_service import (
                    execute_pending_cancellation,
                )

                execute_pending_cancellation(run.id, hermes=hermes)
                return None
            return run.hermes_run_id
        except Exception as exc:  # noqa: BLE001 - retry transient runtime hiccups
            last_exc = exc
            if attempt < 2:
                _set_status(run, "Waiting for the agent runtime…")
                time.sleep(2 * (attempt + 1))
    _fail(run, "Could not reach the agent runtime.", last_exc)
    return None


def _stream_run(hermes, run, interrupted_detail, on_tool=None):
    """
    Consume Hermes's SSE stream. `on_tool(event, preview)` is called per
    completed tool call for audit rows and live status. Hermes owns the agent
    loop limit through its global ``agent.max_turns`` configuration; DARE does
    not count tool events or impose a competing wall-clock execution deadline.
    Returns confirmed output for downstream parsing. Returns None after any
    verified non-completed or unconfirmed state; that state is already persisted.
    """
    chunks = []
    reasoning_parts = []
    last_preview = ""
    terminal_event = ""
    stream_error = None
    try:
        for event in hermes.stream_events(run.hermes_run_id):
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "reasoning.available":
                # The agent's own account of what it read and why it chose it —
                # for a JSON-forced Scout run this is the only place the thinking
                # survives, so keep it as durable observability.
                text = event.get("text")
                if isinstance(text, str) and text.strip():
                    reasoning_parts.append(text.strip())
            elif etype == "tool.started":
                last_preview = event.get("preview") or ""
            elif etype == "tool.completed":
                if on_tool:
                    on_tool(event, last_preview)
            elif etype == "run.completed":
                terminal_event = etype
                break
            elif etype in {"run.failed", "run.cancelled"}:
                terminal_event = etype
                break
    except Exception as exc:  # noqa: BLE001
        stream_error = exc
        logger.warning(
            "Hermes event stream ended unexpectedly for run %s: %s",
            run.hermes_run_id,
            exc,
        )

    # Persist the reasoning trace regardless of how the run resolves — it is
    # captured live from the stream and cannot be re-fetched from the terminal
    # record afterwards.
    if reasoning_parts:
        run.reasoning_trace = "\n\n".join(reasoning_parts)
        run.save(update_fields=["reasoning_trace", "updated_at"])

    # A transport error, a stream that closes without a terminal event, or a
    # terminal stream with no text is ambiguous at the transport layer. Hermes's
    # pollable run record is the verification boundary.
    if stream_error or not terminal_event or not chunks:
        return _verify_terminal_run(hermes, run, interrupted_detail)

    if terminal_event != "run.completed":
        return _verify_terminal_run(hermes, run, interrupted_detail)

    return "".join(chunks)


# ── Scout ────────────────────────────────────────────────────────────────────


def _scout_input(run, task, soul_content):
    """The Scout task plus DARE-side context: the project's research question
    (the bare-minimum framing — what this project is about), credentialed
    results, and approved knowledge."""
    project = run.project
    text = task
    # Always anchor the task in the project's research question — that overall
    # framing is the whole point of research mode, so Scout judges relevance
    # against it rather than treating the task in a vacuum.
    if project.question and project.question.strip():
        question = project.question.strip()
        if question != task.strip():
            text = (
                f"Project research question (overall context): {question}\n\n"
                f"Scout task for this run: {task}"
            )

    # DARE executes the scholar's connected research tools itself (creds and
    # audit stay here) and both logs the calls and injects the results.
    run_tools = (run.selected_context or {}).get("tools") or project.enabled_tools
    try:
        tool_results = gather_tool_results(run.user, run_tools, task)
    except Exception as exc:  # noqa: BLE001 - non-fatal
        logger.warning("Scout MCP context gather failed: %s", exc)
        tool_results = []
    for r in tool_results:
        ResearchAgentToolCall.objects.create(
            run=run,
            tool=f"{r['slug']}__{r['tool']}",
            arguments={"query": task},
            status=(
                AgentToolCallStatus.ERROR if r["error"] else AgentToolCallStatus.SUCCESS
            ),
            # The complete raw response, never trimmed — the Runs view lets the
            # scholar audit exactly what came back; only the prompt injection
            # below is compacted/capped.
            result_summary=r["error"] or r["raw"],
            result_tokens=token_count(r["error"] or r["raw"]),
            error=r["error"],
        )
    context = "\n\n".join(
        f"### {r['slug']} · {r['tool']}\n{r['text']}"
        for r in tool_results
        if r["text"] and not r["error"]
    )
    if context:
        text += (
            "\n\nBelow are credentialed results from the scholar's connected "
            "research tools. Evaluate them against the standards and include "
            "the relevant ones in your staging items (cite them), alongside "
            f"what you find via web_search:\n\n{context}"
        )

    knowledge = _knowledge_block(project)
    if knowledge:
        text += (
            "\n\nThe scholar's approved project knowledge so far — do NOT "
            "re-stage these sources; find new or complementary evidence that "
            f"builds on them:\n{knowledge}"
        )
    if run.thread_id:
        digest = thread_digest(run.thread)
        if digest:
            text += (
                "\n\nThe conversation this Scout was launched from (context "
                "for what the scholar wants; your task above is what to do):\n" + digest
            )
    return text


def _stage_envelope_items(run, items):
    """Fallback: stage candidates a Scout returned in a JSON envelope."""
    return sum(
        1
        for item in items
        if isinstance(item, dict) and stage_candidate(run, item).item is not None
    )


@job("default")
def run_scout_job(run_id):
    """Run a delegated Scout discovery end to end (search → read → staging)."""
    try:
        _run_scout(run_id)
    finally:
        _notify_thread(run_id)


def _run_scout(run_id):
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if not run:
        logger.warning("run_scout_job: run %s not found", run_id)
        return
    if _cancel_before_hermes_start(run):
        return

    soul, version, soul_content = _project_soul(run.project)
    hermes = get_hermes_service(run.project)
    hermes.provision_soul(soul_content)

    _set_status(run, "Querying research tools…")
    scout_input = _scout_input(run, run.task, soul_content)

    _set_status(run, "Starting Scout…")
    quick = (run.selected_context or {}).get("depth") == "quick"
    instructions = build_scout_instructions(
        soul_content,
        run_token(run),
        # Upper bounds, not targets — the brief says stage what the evidence
        # justifies; 1 great source or 10 are both fine.
        max_candidates=3 if quick else 10,
        max_searches=2 if quick else 5,
    )
    if not _start_run(hermes, run, scout_input, instructions):
        return

    seen_ids = set()  # GatewayFetch rows already claimed by this run's audit
    counts = {"searched": 0, "read": 0}

    def on_tool(event, preview):
        # Only the real call preview — substituting the task text here would
        # mislead the Runs audit when Hermes sends none.
        record_tool_call(run, event, {"query": preview}, seen_ids)
        tool = event.get("tool", "")
        if tool.endswith("web_search"):
            counts["searched"] += 1
        elif tool.endswith("fetch_page"):
            counts["read"] += 1
        staged = ResearchStagingItem.active_objects.filter(run=run).count()
        _set_status(
            run,
            f"{counts['searched']} searches · {counts['read']} pages read · "
            f"{staged} staged",
        )

    output = _stream_run(hermes, run, "The Scout run was interrupted.", on_tool)
    if output is None:
        return

    _set_status(run, "Evaluating findings…")
    _stage_envelope_items(run, parse_staging_items(output))
    learning = parse_scout_learning(output)
    if learning:
        merge_learning(run, learning.get("applied"), learning.get("setAside"))
    staged = ResearchStagingItem.active_objects.filter(run=run).count()

    if staged == 0:
        snippet = " ".join(output.split())[:150]
        detail = (
            f"Staged 0 findings — agent replied: “{snippet}…”"
            if snippet
            else "Staged 0 findings."
        )
        _finish(run, detail, hermes, raw_output=output)
    else:
        plural = "s" if staged != 1 else ""
        _finish(run, f"Staged {staged} finding{plural}.", hermes, raw_output=output)

    run.session.last_run_at = timezone.now()
    run.session.save(update_fields=["last_run_at", "updated_at"])


# ── Critic ───────────────────────────────────────────────────────────────────


@job("default")
def run_critic_job(run_id, item_id):
    """Pressure-test a staged source against the standards, attaching a verdict."""
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    item = ResearchStagingItem.objects.filter(id=item_id).first()
    if not run or not item:
        logger.warning("run_critic_job: run %s / item %s not found", run_id, item_id)
        return
    if _cancel_before_hermes_start(run):
        return

    _, _, soul_content = _project_soul(item.project)
    hermes = get_hermes_service(run.project)
    hermes.provision_soul(soul_content)

    _set_status(run, "Reading the source…")
    if not _start_run(
        hermes, run, critic_input(item), build_critic_instructions(soul_content)
    ):
        return

    seen_ids = set()  # GatewayFetch rows already claimed by this run's audit

    def on_tool(event, preview):
        record_tool_call(run, event, {"itemId": item.id, "query": preview}, seen_ids)
        _set_status(run, "Assessing the source…")

    output = _stream_run(hermes, run, "The Critic run was interrupted.", on_tool)
    if output is None:
        return

    verdict = parse_critic_verdict(output)
    if verdict:
        item.critic_metadata = {
            **verdict,
            "runId": run.id,
            "assessedAt": timezone.now().isoformat(),
        }
        item.save(update_fields=["critic_metadata", "updated_at"])
        _finish(run, f"Critic: {verdict['verdict']}.", hermes, raw_output=output)
    else:
        _finish(run, "Critic could not return a verdict.", hermes, raw_output=output)


# ── Artifacts ────────────────────────────────────────────────────────────────


@job("default")
def run_artifact_job(run_id):
    """Generate a renderable artifact via the JSON contract and persist it."""
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if not run:
        logger.warning("run_artifact_job: run %s not found", run_id)
        return
    if _cancel_before_hermes_start(run):
        return

    _, _, soul_content = _project_soul(run.project)
    hermes = get_hermes_service(run.project)
    hermes.provision_soul(soul_content)

    artifact_input = run.task
    if run.project.question:
        artifact_input += f"\n\nThe project's research question: {run.project.question}"
    knowledge = _knowledge_block(run.project)
    if knowledge:
        artifact_input += (
            "\n\nGround the artifact in the scholar's approved project "
            f"knowledge:\n{knowledge}"
        )

    _set_status(run, "Generating artifact…")
    artifact_type = (run.selected_context or {}).get("artifactType", "")
    instructions = build_artifact_instructions(soul_content, artifact_type)
    if not _start_run(hermes, run, artifact_input, instructions):
        return

    # Record the agent's tool calls (web_search, fetch_page, vision_analyze, …)
    # so a long artifact run isn't an opaque black box in the Runs audit.
    seen_ids = set()

    def on_tool(event, preview):
        record_tool_call(run, event, {"query": preview}, seen_ids)

    output = _stream_run(hermes, run, "The artifact run was interrupted.", on_tool)
    if output is None:
        return

    artifacts = parse_artifacts(output)
    now = timezone.now()
    created = 0
    for art in artifacts:
        try:
            ResearchArtifact.objects.create(
                project=run.project,
                run=run,
                artifact_type=art["artifact_type"],
                title=art["title"][:255],
                content=art["content"],
                source="hermes",
                provenance={"runId": run.id, "retrievedAt": now.isoformat()},
            )
            created += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Artifact create failed: %s", exc)

    plural = "s" if created != 1 else ""
    _finish(
        run,
        (
            f"Generated {created} artifact{plural}."
            if created
            else "No artifact produced."
        ),
        hermes,
        raw_output=output,
    )


# ── Chat follow-ups ──────────────────────────────────────────────────────────


@job("default")
def run_thread_followup_job(thread_id):
    """Let the agent react to thread events nobody has answered yet.

    Safe to enqueue more than once: it does nothing when a turn is already
    live (that turn's end re-enqueues) or when every event has been answered.
    """
    thread = (
        ResearchSession.active_objects.filter(id=thread_id)
        .select_related("project", "user")
        .first()
    )
    if thread is None or thread_has_active_turn(thread) or not unseen_events(thread):
        return
    try:
        run, hermes = start_turn(thread, thread.user, "", followup=True)
    except TurnStartFailed:
        return
    for _ in stream_turn(run, hermes):
        pass
