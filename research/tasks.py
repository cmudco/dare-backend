"""
Background jobs for the Research app.

Scout runs are long (web search + synthesis), so they run on django-rq rather
than tying up a request. The job streams Hermes events, keeps the run's live
status fresh, logs tool-call provenance, and stages the parsed source candidates.
The frontend polls the run for status; findings appear in the Review Inbox.
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


def _set_status(run, detail):
    run.status_detail = detail[:255]
    run.save(update_fields=["status_detail", "updated_at"])


def _fail(run, detail, exc):
    logger.error("Scout run %s failed: %s", run.id, exc)
    run.status = AgentRunStatus.FAILED
    run.error = str(exc)
    run.status_detail = detail
    run.completed_at = timezone.now()
    run.save(
        update_fields=["status", "error", "status_detail", "completed_at", "updated_at"]
    )


def _session_key(project):
    """The workspace's stable Hermes memory scope (X-Hermes-Session-Key)."""
    return f"dare-proj{project.id}"


def _run_session_id(run):
    """
    A fresh Hermes session per delegated run. Delegated runs are standalone
    tasks: sharing one session made every run replay the mode's whole history
    (including failed attempts) on every loop turn — the main token inflater.
    Cross-run recall survives via Hermes's session summaries + search, and
    long-term memory is scoped by the session KEY, not the session id.
    """
    return f"{run.session.hermes_session_id}-r{run.id}"


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


# Hard per-run budget: a delegated run that exceeds either bound is stopped,
# not left to burn tokens. (Hermes-side agent.max_turns caps the loop too.)
# Sized to fit a full deep Scout (up to 5 searches + 10 reads) with headroom.
MAX_RUN_TOOL_CALLS = 18
MAX_RUN_SECONDS = 480


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


def _stop_over_budget(hermes, hermes_run_id, run, reason):
    hermes.stop_run(hermes_run_id)
    _fail(
        run,
        f"Stopped: the run exceeded its budget ({reason}).",
        Exception(f"run budget exceeded: {reason}"),
    )


def _hermes_run_failed(hermes, hermes_run_id, chunks):
    """
    True when the run produced nothing because Hermes itself failed (e.g. the
    brain hit a rate limit) — without this check an empty stream would be
    reported as a successful run with no findings.
    """
    if chunks:
        return False
    try:
        return hermes.get_run(hermes_run_id).get("status") == "failed"
    except Exception:  # noqa: BLE001 - can't confirm; let parsing decide
        return False


def _reask_json(hermes, session_id, expectation):
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
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001 - repair is best-effort
        logger.warning("Corrective re-ask could not start: %s", exc)
        return ""
    chunks = []
    try:
        for event in hermes.stream_events(started["run_id"]):
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("Corrective re-ask stream failed: %s", exc)
    return "".join(chunks)


@job("default")
def run_scout_job(run_id):
    """Run a delegated Scout discovery end to end (Hermes web search -> staging)."""
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if not run:
        logger.warning("run_scout_job: run %s not found", run_id)
        return

    project = run.project
    task = run.task
    soul = SoulFile.active_objects.filter(project=project).first()
    version = soul.current_version() if soul else None
    soul_content = version.content if version else ""

    hermes = get_hermes_service()
    hermes.provision_soul(soul_content)

    # DARE executes the scholar's connected research MCP tools (Consensus, …) and
    # feeds the credentialed results into the Scout run. Creds + audit stay in
    # DARE; Hermes structures these alongside its own web_search into staging.
    _set_status(run, "Querying research tools…")
    scout_input = task
    # The composer's per-run selection narrows the project's enabled set.
    run_tools = (run.selected_context or {}).get("tools") or project.enabled_tools
    try:
        tool_results = gather_tool_results(run.user, run_tools, task)
    except Exception as exc:  # noqa: BLE001 - non-fatal
        logger.warning("Scout MCP context gather failed: %s", exc)
        tool_results = []
    # These credentialed pre-fetch calls are part of the run's audit trail too —
    # log them with a result preview (or the error) so the Runs view shows what
    # actually came back. Failed calls are never injected as evidence.
    for r in tool_results:
        ResearchAgentToolCall.objects.create(
            run=run,
            tool=f"{r['slug']}__{r['tool']}",
            arguments={"query": task},
            status=(
                AgentToolCallStatus.ERROR if r["error"] else AgentToolCallStatus.SUCCESS
            ),
            # The tool's complete raw response — never trimmed. The Runs view
            # lets the scholar expand a call and audit exactly what came back;
            # only the prompt injection is compacted/capped, not the record.
            result_summary=r["error"] or r["raw"],
            error=r["error"],
        )
    mcp_context = "\n\n".join(
        f"### {r['slug']} · {r['tool']}\n{r['text']}"
        for r in tool_results
        if r["text"] and not r["error"]
    )
    if mcp_context:
        scout_input = (
            f"{task}\n\n"
            "Below are credentialed results from the scholar's connected research "
            "tools. Evaluate them against the standards and include the relevant "
            "ones in your staging items (cite them), alongside what you find via "
            f"web_search:\n\n{mcp_context}"
        )

    knowledge = _knowledge_block(project)
    if knowledge:
        scout_input += (
            "\n\nThe scholar's approved project knowledge so far — do NOT "
            "re-stage these sources; find new or complementary evidence that "
            f"builds on them:\n{knowledge}"
        )

    _set_status(run, "Starting Scout…")
    session_id = _run_session_id(run)
    # Quick runs run a small budget; deep runs a generous one — real research
    # deserves a real result set (each fetched page costs input tokens, but
    # the per-run budget caps the blast radius).
    quick = (run.selected_context or {}).get("depth") == "quick"
    try:
        started = hermes.start_run(
            input_text=scout_input,
            instructions=build_scout_instructions(
                soul_content,
                # Upper bounds, not targets — the brief says stage what the
                # evidence justifies; 1 great source or 10 are both fine.
                max_candidates=3 if quick else 10,
                max_searches=2 if quick else 5,
                allowed_tools=run_tools,
            ),
            session_id=session_id,
            session_key=_session_key(project),
        )
        hermes_run_id = started["run_id"]
    except Exception as exc:  # noqa: BLE001
        _fail(run, "Could not reach the agent runtime.", exc)
        return

    run.hermes_run_id = hermes_run_id
    run.save(update_fields=["hermes_run_id", "updated_at"])

    chunks = []
    searches = 0
    last_preview = ""
    stopped_early = ""
    budget = _RunBudget()
    try:
        for event in hermes.stream_events(hermes_run_id):
            over = budget.exceeded()
            if over:
                # Stop the burn, but salvage: whatever the agent compiled so
                # far still gets parsed and staged below.
                stopped_early = over
                hermes.stop_run(hermes_run_id)
                break
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "tool.started":
                last_preview = event.get("preview") or ""
                _set_status(run, "Searching the web…")
            elif etype == "tool.completed":
                searches += 1
                budget.tool_calls += 1
                duration = event.get("duration")
                ResearchAgentToolCall.objects.create(
                    run=run,
                    tool=event.get("tool", ""),
                    # Only the real call preview — substituting the task text
                    # here misleads the Runs audit when Hermes sends none.
                    arguments={"query": last_preview},
                    status=(
                        AgentToolCallStatus.ERROR
                        if event.get("error")
                        else AgentToolCallStatus.SUCCESS
                    ),
                    duration_ms=int(duration * 1000) if duration else None,
                    error=event.get("error") or "",
                )
                plural = "s" if searches != 1 else ""
                _set_status(run, f"Searched {searches} source{plural}…")
            elif etype == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        _fail(run, "The Scout run was interrupted.", exc)
        return

    if _hermes_run_failed(hermes, hermes_run_id, chunks):
        _fail(run, "The agent runtime failed mid-run.", Exception("hermes run failed"))
        return

    _set_status(run, "Evaluating findings…")
    output = "".join(chunks)
    items = parse_staging_items(output)
    if not items and output.strip() and not stopped_early:
        _set_status(run, "Repairing the result format…")
        items = parse_staging_items(
            _reask_json(
                hermes,
                session_id,
                'the {"stagingItems": [...]} object from your instructions',
            )
        )
    now = timezone.now()
    staged = 0
    for item in items:
        year = item.get("year")
        confidence = item.get("confidence")
        try:
            ResearchStagingItem.objects.create(
                project=project,
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
                    "query": task,
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

    plural = "s" if staged != 1 else ""
    run.status = AgentRunStatus.COMPLETED
    if stopped_early:
        run.status_detail = (
            f"Stopped at the run budget ({stopped_early}) — "
            f"staged {staged} finding{plural} from what was gathered."
        )
        if not staged:
            run.status = AgentRunStatus.FAILED
    elif staged == 0:
        # Surface what the agent actually said — a vague ask ("hello") or a
        # deliberate no-result should be readable on the run card, not opaque.
        snippet = " ".join(output.split())[:150]
        run.status_detail = (
            f"Staged 0 findings — agent replied: “{snippet}…”"
            if snippet
            else "Staged 0 findings."
        )
    else:
        run.status_detail = f"Staged {staged} finding{plural}."
    run.completed_at = now
    run.usage = hermes.fetch_usage(hermes_run_id)
    run.save(
        update_fields=["status", "status_detail", "completed_at", "usage", "updated_at"]
    )
    run.session.last_run_at = now
    run.session.save(update_fields=["last_run_at", "updated_at"])


@job("default")
def run_critic_job(run_id, item_id):
    """Pressure-test a staged source against the standards, attaching a verdict."""
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    item = ResearchStagingItem.objects.filter(id=item_id).first()
    if not run or not item:
        logger.warning("run_critic_job: run %s / item %s not found", run_id, item_id)
        return

    soul = SoulFile.active_objects.filter(project=item.project).first()
    version = soul.current_version() if soul else None
    soul_content = version.content if version else ""

    hermes = get_hermes_service()
    hermes.provision_soul(soul_content)

    _set_status(run, "Reading the source…")
    session_id = _run_session_id(run)
    try:
        started = hermes.start_run(
            input_text=critic_input(item),
            instructions=build_critic_instructions(soul_content),
            session_id=session_id,
            session_key=_session_key(item.project),
        )
        hermes_run_id = started["run_id"]
    except Exception as exc:  # noqa: BLE001
        _fail(run, "Could not reach the agent runtime.", exc)
        return

    run.hermes_run_id = hermes_run_id
    run.save(update_fields=["hermes_run_id", "updated_at"])

    chunks = []
    last_preview = ""
    budget = _RunBudget()
    try:
        for event in hermes.stream_events(hermes_run_id):
            over = budget.exceeded()
            if over:
                _stop_over_budget(hermes, hermes_run_id, run, over)
                return
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "tool.started":
                last_preview = event.get("preview") or ""
                _set_status(run, "Reading the source…")
            elif etype == "tool.completed":
                budget.tool_calls += 1
                duration = event.get("duration")
                ResearchAgentToolCall.objects.create(
                    run=run,
                    tool=event.get("tool", ""),
                    arguments={"itemId": item.id, "query": last_preview},
                    status=(
                        AgentToolCallStatus.ERROR
                        if event.get("error")
                        else AgentToolCallStatus.SUCCESS
                    ),
                    duration_ms=int(duration * 1000) if duration else None,
                    error=event.get("error") or "",
                )
                _set_status(run, "Assessing the source…")
            elif etype == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        _fail(run, "The Critic run was interrupted.", exc)
        return

    if _hermes_run_failed(hermes, hermes_run_id, chunks):
        _fail(run, "The agent runtime failed mid-run.", Exception("hermes run failed"))
        return

    now = timezone.now()
    output = "".join(chunks)
    verdict = parse_critic_verdict(output)
    if not verdict and output.strip():
        verdict = parse_critic_verdict(
            _reask_json(
                hermes,
                session_id,
                'the {"verdict": ..., "reasoning": ..., "concerns": [...]} '
                "object from your instructions",
            )
        )
    if verdict:
        item.critic_metadata = {
            **verdict,
            "runId": run.id,
            "assessedAt": now.isoformat(),
        }
        item.save(update_fields=["critic_metadata", "updated_at"])
        detail = f"Critic: {verdict['verdict']}."
    else:
        detail = "Critic could not return a verdict."

    run.status = AgentRunStatus.COMPLETED
    run.status_detail = detail
    run.completed_at = now
    run.usage = hermes.fetch_usage(hermes_run_id)
    run.save(
        update_fields=["status", "status_detail", "completed_at", "usage", "updated_at"]
    )


@job("default")
def run_artifact_job(run_id):
    """Generate a renderable artifact via the JSON contract and persist it."""
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if not run:
        logger.warning("run_artifact_job: run %s not found", run_id)
        return

    project = run.project
    artifact_type = (run.selected_context or {}).get("artifactType", "")
    soul = SoulFile.active_objects.filter(project=project).first()
    version = soul.current_version() if soul else None
    soul_content = version.content if version else ""

    hermes = get_hermes_service()
    hermes.provision_soul(soul_content)

    artifact_input = run.task
    knowledge = _knowledge_block(project)
    if knowledge:
        artifact_input += (
            "\n\nGround the artifact in the scholar's approved project "
            f"knowledge:\n{knowledge}"
        )

    _set_status(run, "Generating artifact…")
    session_id = _run_session_id(run)
    try:
        started = hermes.start_run(
            input_text=artifact_input,
            instructions=build_artifact_instructions(soul_content, artifact_type),
            session_id=session_id,
            session_key=_session_key(project),
        )
        hermes_run_id = started["run_id"]
    except Exception as exc:  # noqa: BLE001
        _fail(run, "Could not reach the agent runtime.", exc)
        return

    run.hermes_run_id = hermes_run_id
    run.save(update_fields=["hermes_run_id", "updated_at"])

    chunks = []
    budget = _RunBudget()
    try:
        for event in hermes.stream_events(hermes_run_id):
            over = budget.exceeded()
            if over:
                _stop_over_budget(hermes, hermes_run_id, run, over)
                return
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "tool.completed":
                budget.tool_calls += 1
            elif etype == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        _fail(run, "The artifact run was interrupted.", exc)
        return

    if _hermes_run_failed(hermes, hermes_run_id, chunks):
        _fail(run, "The agent runtime failed mid-run.", Exception("hermes run failed"))
        return

    now = timezone.now()
    output = "".join(chunks)
    problems = []
    artifacts = parse_artifacts(output, errors=problems)
    # Re-ask only on actual format problems — a valid empty envelope is the
    # model deliberately declining a no-substance request, not a parse failure.
    if not artifacts and output.strip() and problems:
        _set_status(run, "Repairing the artifact format…")
        expectation = (
            'the {"artifacts": [{"type": ..., "title": ..., "content": ...}]} '
            "object from your instructions"
        )
        if problems:
            expectation += ". Fix these specific problems: " + "; ".join(problems[:5])
        artifacts = parse_artifacts(_reask_json(hermes, session_id, expectation))
    created = 0
    for art in artifacts:
        try:
            ResearchArtifact.objects.create(
                project=project,
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
    run.status = AgentRunStatus.COMPLETED
    run.status_detail = (
        f"Generated {created} artifact{plural}." if created else "No artifact produced."
    )
    run.completed_at = now
    run.usage = hermes.fetch_usage(hermes_run_id)
    run.save(
        update_fields=["status", "status_detail", "completed_at", "usage", "updated_at"]
    )
