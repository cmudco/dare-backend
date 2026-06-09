"""
Background jobs for the Research app.

Scout runs are long (web search + synthesis), so they run on django-rq rather
than tying up a request. The job streams Hermes events, keeps the run's live
status fresh, logs tool-call provenance, and stages the parsed source candidates.
The frontend polls the run for status; findings appear in the Review Inbox.
"""

import logging

from django.utils import timezone
from django_rq import job

from research.constants import (
    AgentRunStatus,
    AgentToolCallStatus,
    StagingItemStatus,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchArtifact,
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
from mcp.services.mcp_gateway import gather_tool_context

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
    try:
        mcp_context = gather_tool_context(run.user, project.enabled_tools, task)
    except Exception as exc:  # noqa: BLE001 - non-fatal
        logger.warning("Scout MCP context gather failed: %s", exc)
        mcp_context = ""
    if mcp_context:
        scout_input = (
            f"{task}\n\n"
            "Below are credentialed results from the scholar's connected research "
            "tools. Evaluate them against the standards and include the relevant "
            "ones in your staging items (cite them), alongside what you find via "
            f"web_search:\n\n{mcp_context}"
        )

    _set_status(run, "Starting Scout…")
    try:
        started = hermes.start_run(
            input_text=scout_input,
            instructions=build_scout_instructions(soul_content),
            session_id=run.session.hermes_session_id,
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
    try:
        for event in hermes.stream_events(hermes_run_id):
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "tool.started":
                last_preview = event.get("preview") or ""
                _set_status(run, "Searching the web…")
            elif etype == "tool.completed":
                searches += 1
                duration = event.get("duration")
                ResearchAgentToolCall.objects.create(
                    run=run,
                    tool=event.get("tool", ""),
                    arguments={"query": last_preview or task},
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

    _set_status(run, "Evaluating findings…")
    now = timezone.now()
    staged = 0
    for item in parse_staging_items("".join(chunks)):
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
                    "tool": "web",
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
    try:
        started = hermes.start_run(
            input_text=critic_input(item),
            instructions=build_critic_instructions(soul_content),
            session_id=run.session.hermes_session_id,
        )
        hermes_run_id = started["run_id"]
    except Exception as exc:  # noqa: BLE001
        _fail(run, "Could not reach the agent runtime.", exc)
        return

    run.hermes_run_id = hermes_run_id
    run.save(update_fields=["hermes_run_id", "updated_at"])

    chunks = []
    last_preview = ""
    try:
        for event in hermes.stream_events(hermes_run_id):
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "tool.started":
                last_preview = event.get("preview") or ""
                _set_status(run, "Reading the source…")
            elif etype == "tool.completed":
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

    now = timezone.now()
    verdict = parse_critic_verdict("".join(chunks))
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

    _set_status(run, "Generating artifact…")
    try:
        started = hermes.start_run(
            input_text=run.task,
            instructions=build_artifact_instructions(soul_content, artifact_type),
            session_id=run.session.hermes_session_id,
        )
        hermes_run_id = started["run_id"]
    except Exception as exc:  # noqa: BLE001
        _fail(run, "Could not reach the agent runtime.", exc)
        return

    run.hermes_run_id = hermes_run_id
    run.save(update_fields=["hermes_run_id", "updated_at"])

    chunks = []
    try:
        for event in hermes.stream_events(hermes_run_id):
            etype = event.get("event")
            if etype == "message.delta":
                chunks.append(event.get("delta", ""))
            elif etype == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        _fail(run, "The artifact run was interrupted.", exc)
        return

    now = timezone.now()
    created = 0
    for art in parse_artifacts("".join(chunks)):
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
