"""
Background jobs for the Research app.

Delegated runs are long (search + reading + synthesis), so they run on
django-rq rather than tying up a request. Every job follows the same shape:
prepare (soul + input) → start the Hermes run → stream its events under a hard
budget → parse the structured reply → persist results. The frontend polls the
run for live status; only DARE writes to the database.
"""

import logging
import time

from django.utils import timezone
from django_rq import job

from mcp.services.mcp_gateway import gather_tool_results
from research.constants import AgentRunStatus, AgentToolCallStatus, StagingItemStatus
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchArtifact,
    ResearchKnowledgeItem,
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
    parse_staging_items,
)

logger = logging.getLogger(__name__)

# Hard per-run budget: a delegated run that exceeds either bound is stopped,
# not left to burn tokens. (Hermes-side agent.max_turns caps the loop too.)
# Sized to fit a full deep Scout (up to 5 searches + 10 reads) with headroom.
MAX_RUN_TOOL_CALLS = 18
MAX_RUN_SECONDS = 480


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


def _finish(run, detail, hermes, failed=False):
    """Close out a run: final status + detail + token usage, in one save."""
    run.status = AgentRunStatus.FAILED if failed else AgentRunStatus.COMPLETED
    run.status_detail = detail[:255]
    run.completed_at = timezone.now()
    run.usage = hermes.fetch_usage(run.hermes_run_id)
    run.save(
        update_fields=["status", "status_detail", "completed_at", "usage", "updated_at"]
    )


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
    try:
        started = hermes.start_run(
            input_text=input_text,
            instructions=instructions,
            session_id=f"{run.session.hermes_session_id}-r{run.id}",
            session_key=f"dare-proj{run.project_id}",
        )
    except Exception as exc:  # noqa: BLE001
        _fail(run, "Could not reach the agent runtime.", exc)
        return None
    run.hermes_run_id = started["run_id"]
    run.save(update_fields=["hermes_run_id", "updated_at"])
    return run.hermes_run_id


def _record_tool_call(run, event, arguments):
    """Persist one streamed tool call for the Runs audit view."""
    duration = event.get("duration")
    ResearchAgentToolCall.objects.create(
        run=run,
        tool=event.get("tool", ""),
        arguments=arguments,
        status=(
            AgentToolCallStatus.ERROR
            if event.get("error")
            else AgentToolCallStatus.SUCCESS
        ),
        duration_ms=int(duration * 1000) if duration else None,
        error=event.get("error") or "",
    )


class _RunBudget:
    """Tracks a delegated run's budget; trips when either bound is exceeded."""

    def __init__(self):
        self.started = time.monotonic()
        self.tool_calls = 0

    def exceeded(self):
        if self.tool_calls > MAX_RUN_TOOL_CALLS:
            return f"more than {MAX_RUN_TOOL_CALLS} tool calls"
        if time.monotonic() - self.started > MAX_RUN_SECONDS:
            return f"more than {MAX_RUN_SECONDS // 60} minutes"
        return ""


def _stream_run(hermes, run, interrupted_detail, on_tool=None):
    """
    Consume the run's SSE stream under the budget. `on_tool(event, preview)`
    is called per completed tool call (for audit rows + live status). Returns
    (output, stopped_reason) — stopped_reason is set when the budget tripped
    (the Hermes run is stopped, but the partial output is still returned so
    callers can salvage it). Returns None when the run already failed.
    """
    chunks = []
    last_preview = ""
    stopped = ""
    budget = _RunBudget()
    try:
        for event in hermes.stream_events(run.hermes_run_id):
            stopped = budget.exceeded()
            if stopped:
                hermes.stop_run(run.hermes_run_id)
                break
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "tool.started":
                last_preview = event.get("preview") or ""
            elif etype == "tool.completed":
                budget.tool_calls += 1
                if on_tool:
                    on_tool(event, last_preview)
            elif etype == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        _fail(run, interrupted_detail, exc)
        return None

    # An empty stream can mean Hermes itself died (e.g. the brain hit a rate
    # limit) — never report that as a successful run with no findings.
    if not chunks:
        try:
            hermes_failed = hermes.get_run(run.hermes_run_id).get("status") == "failed"
        except Exception:  # noqa: BLE001 - can't confirm; let parsing decide
            hermes_failed = False
        if hermes_failed:
            _fail(run, "The agent runtime failed mid-run.", Exception("hermes failed"))
            return None

    return "".join(chunks), stopped


def _reask_json(hermes, run, expectation):
    """
    One corrective re-ask when a structured reply wasn't parseable. Hermes's
    API has no schema forcing — the contract is prompt-level — so the official
    pattern is: parse defensively, then ask once for a repaired reply in the
    same session (the model still has its previous answer in context).
    """
    try:
        started = hermes.start_run(
            input_text=(
                "Your previous reply was not parseable. Return ONLY "
                + expectation
                + " — a single JSON object, no prose, no markdown fences."
            ),
            instructions="",
            session_id=f"{run.session.hermes_session_id}-r{run.id}",
        )
    except Exception as exc:  # noqa: BLE001 - repair is best-effort
        logger.warning("Corrective re-ask could not start: %s", exc)
        return ""
    chunks = []
    try:
        for event in hermes.stream_events(started["run_id"]):
            if event.get("event") == "message.delta":
                chunks.append(event.get("delta", ""))
            elif event.get("event") == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("Corrective re-ask stream failed: %s", exc)
    return "".join(chunks)


# ── Scout ────────────────────────────────────────────────────────────────────


def _scout_input(run, task, soul_content):
    """The Scout task plus DARE-side context: credentialed results + knowledge."""
    project = run.project
    text = task

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
    return text, run_tools


def _stage_items(run, items, soul, version):
    """Persist Scout's candidates, coercing types at the DB boundary."""
    now = timezone.now()
    staged = 0
    for item in items:
        year = item.get("year")
        confidence = item.get("confidence")
        try:
            ResearchStagingItem.objects.create(
                project=run.project,
                run=run,
                title=str(item.get("title") or "")[:512],
                authors=str(item.get("authors") or "")[:512],
                year=year if isinstance(year, int) else None,
                venue=str(item.get("venue") or "")[:255],
                doi=str(item.get("doi") or "")[:255],
                url=str(item.get("url") or "")[:1024],
                rationale=str(item.get("rationale") or ""),
                confidence=(
                    float(confidence) if isinstance(confidence, (int, float)) else None
                ),
                confidence_rationale=str(item.get("confidenceRationale") or ""),
                evidence_label=str(item.get("evidenceLabel") or "")[:32],
                citation_context=str(item.get("citationContext") or ""),
                status=StagingItemStatus.STAGED,
                provenance={
                    # The agent declares which search surfaced each candidate —
                    # only it can see its in-loop tool results.
                    "tool": str(item.get("sourceTool") or "web")[:32],
                    "query": run.task,
                    "retrievedAt": now.isoformat(),
                    "soulFileId": soul.id if soul else None,
                    "soulFileVersion": version.version if version else None,
                    "role": "scout",
                    "runId": run.id,
                },
            )
            staged += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scout staging item create failed: %s", exc)
    return staged


@job("default")
def run_scout_job(run_id):
    """Run a delegated Scout discovery end to end (search → read → staging)."""
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if not run:
        logger.warning("run_scout_job: run %s not found", run_id)
        return

    soul, version, soul_content = _project_soul(run.project)
    hermes = get_hermes_service()
    hermes.provision_soul(soul_content)

    _set_status(run, "Querying research tools…")
    scout_input, run_tools = _scout_input(run, run.task, soul_content)

    _set_status(run, "Starting Scout…")
    quick = (run.selected_context or {}).get("depth") == "quick"
    instructions = build_scout_instructions(
        soul_content,
        # Upper bounds, not targets — the brief says stage what the evidence
        # justifies; 1 great source or 10 are both fine.
        max_candidates=3 if quick else 10,
        max_searches=2 if quick else 5,
        allowed_tools=run_tools,
    )
    if not _start_run(hermes, run, scout_input, instructions):
        return

    searches = 0

    def on_tool(event, preview):
        nonlocal searches
        searches += 1
        # Only the real call preview — substituting the task text here would
        # mislead the Runs audit when Hermes sends none.
        _record_tool_call(run, event, {"query": preview})
        _set_status(run, f"Searched {searches} source{'s' if searches != 1 else ''}…")

    streamed = _stream_run(hermes, run, "The Scout run was interrupted.", on_tool)
    if streamed is None:
        return
    output, stopped = streamed

    _set_status(run, "Evaluating findings…")
    items = parse_staging_items(output)
    if not items and output.strip() and not stopped:
        _set_status(run, "Repairing the result format…")
        items = parse_staging_items(
            _reask_json(
                hermes, run, 'the {"stagingItems": [...]} object from your instructions'
            )
        )
    staged = _stage_items(run, items, soul, version)

    plural = "s" if staged != 1 else ""
    if stopped:
        # Budget-stopped runs salvage what was gathered; only a fully empty
        # salvage counts as failure.
        detail = (
            f"Stopped at the run budget ({stopped}) — "
            f"staged {staged} finding{plural} from what was gathered."
        )
        _finish(run, detail, hermes, failed=not staged)
    elif staged == 0:
        # Surface what the agent actually said — a vague ask ("hello") or a
        # deliberate no-result should be readable on the run card, not opaque.
        snippet = " ".join(output.split())[:150]
        detail = (
            f"Staged 0 findings — agent replied: “{snippet}…”"
            if snippet
            else "Staged 0 findings."
        )
        _finish(run, detail, hermes)
    else:
        _finish(run, f"Staged {staged} finding{plural}.", hermes)

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

    _, _, soul_content = _project_soul(item.project)
    hermes = get_hermes_service()
    hermes.provision_soul(soul_content)

    _set_status(run, "Reading the source…")
    if not _start_run(
        hermes, run, critic_input(item), build_critic_instructions(soul_content)
    ):
        return

    def on_tool(event, preview):
        _record_tool_call(run, event, {"itemId": item.id, "query": preview})
        _set_status(run, "Assessing the source…")

    streamed = _stream_run(hermes, run, "The Critic run was interrupted.", on_tool)
    if streamed is None:
        return
    output, stopped = streamed
    if stopped:
        _finish(
            run,
            f"Stopped: the run exceeded its budget ({stopped}).",
            hermes,
            failed=True,
        )
        return

    verdict = parse_critic_verdict(output)
    if not verdict and output.strip():
        verdict = parse_critic_verdict(
            _reask_json(
                hermes,
                run,
                'the {"verdict": ..., "reasoning": ..., "concerns": [...]} '
                "object from your instructions",
            )
        )
    if verdict:
        item.critic_metadata = {
            **verdict,
            "runId": run.id,
            "assessedAt": timezone.now().isoformat(),
        }
        item.save(update_fields=["critic_metadata", "updated_at"])
        _finish(run, f"Critic: {verdict['verdict']}.", hermes)
    else:
        _finish(run, "Critic could not return a verdict.", hermes)


# ── Artifacts ────────────────────────────────────────────────────────────────


@job("default")
def run_artifact_job(run_id):
    """Generate a renderable artifact via the JSON contract and persist it."""
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if not run:
        logger.warning("run_artifact_job: run %s not found", run_id)
        return

    _, _, soul_content = _project_soul(run.project)
    hermes = get_hermes_service()
    hermes.provision_soul(soul_content)

    artifact_input = run.task
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

    streamed = _stream_run(hermes, run, "The artifact run was interrupted.")
    if streamed is None:
        return
    output, stopped = streamed
    if stopped:
        _finish(
            run,
            f"Stopped: the run exceeded its budget ({stopped}).",
            hermes,
            failed=True,
        )
        return

    problems = []
    artifacts = parse_artifacts(output, errors=problems)
    # Re-ask only on actual format problems — a valid empty envelope is the
    # model deliberately declining a no-substance request, not a parse failure.
    if not artifacts and output.strip() and problems:
        _set_status(run, "Repairing the artifact format…")
        expectation = (
            'the {"artifacts": [{"type": ..., "title": ..., "content": ...}]} '
            "object from your instructions"
            ". Fix these specific problems: " + "; ".join(problems[:5])
        )
        artifacts = parse_artifacts(_reask_json(hermes, run, expectation))

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
    )
