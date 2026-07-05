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

from mcp.models import GatewayFetch
from mcp.services.mcp_gateway import gather_tool_results
from research.constants import AgentRunStatus, AgentToolCallStatus, StagingItemStatus
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchArtifact,
    ResearchKnowledgeItem,
    ResearchProjectMemory,
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


def _project_memory_block(project, max_items=20, per_item_chars=400):
    """The project's durable memory (working theses, decisions, open questions) —
    DARE-owned and per-project, injected per run so the agent carries it across
    sessions. Scoped to this project, so it never leaks into another one."""
    items = ResearchProjectMemory.active_objects.filter(project=project)[:max_items]
    lines = [f"- {m.label}: {(m.detail or '').strip()[:per_item_chars]}" for m in items]
    return "\n".join(lines)


def _start_run(hermes, run, input_text, instructions):
    """
    Start the Hermes run and record its id. Each delegated run gets a fresh
    session (sharing one made every run replay the mode's whole history on
    every loop turn — the main token inflater; cross-run recall survives via
    Hermes's session summaries), while the session KEY pins long-term memory
    to the project. Returns the Hermes run id, or None after failing the run.
    """
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
            return run.hermes_run_id
        except Exception as exc:  # noqa: BLE001 - retry transient runtime hiccups
            last_exc = exc
            if attempt < 2:
                _set_status(run, "Waiting for the agent runtime…")
                time.sleep(2 * (attempt + 1))
    _fail(run, "Could not reach the agent runtime.", last_exc)
    return None


_GATEWAY_PREFIX = "mcp_dare_"
_encoder = None


def _token_count(text):
    """Token size of `text` (cl100k_base) — the per-call context contribution.
    Best-effort: falls back to a chars/4 estimate if the tokenizer is absent."""
    global _encoder
    if not text:
        return 0
    if _encoder is None:
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - estimate rather than fail the run
            _encoder = False
    if _encoder:
        return len(_encoder.encode(text))
    return len(text) // 4


def _run_session_key(run):
    """The Hermes session id DARE assigned this run (see `_start_run`) — the
    value the runtime forwards as X-DARE-Run-Session so gateway rows can be
    attributed to this exact run."""
    return f"{run.session.hermes_session_id}-r{run.id}"


def _next_gateway_fetch(run, tool, seen_ids):
    """The GatewayFetch row this streamed gateway call produced, consumed once.

    One row is written per gateway call (its content on success, the reason on a
    page miss). We attribute a streamed tool event to a row by, in order:
      1. exact run-key match — when the runtime forwards this run's session id;
      2. else the earliest unconsumed *unattributed* row for this tool in the
         run window, tracked via `seen_ids` so N calls map to N rows in order.

    The old code always took the most-recent matching row, which mislabelled
    calls that shared a time window (and, when a call wrote no row at all,
    borrowed a different call's reason — the "same URL ×N" audit artifact).
    Returns None for tools that never touch the gateway. Cross-run bleed is only
    possible on the untagged fallback path; once the runtime forwards the run
    key, attribution is exact even under concurrent runs."""
    if not tool.startswith(_GATEWAY_PREFIX):
        return None
    base = tool[len(_GATEWAY_PREFIX) :]
    window = GatewayFetch.all_objects.filter(
        tool=base, created_at__gte=run.started_at
    ).exclude(id__in=seen_ids)
    row = (
        window.filter(run_key=_run_session_key(run)).order_by("created_at").first()
        or window.filter(run_key="").order_by("created_at").first()
    )
    if row is not None:
        seen_ids.add(row.id)
    return row


def _record_tool_call(run, event, arguments, seen_ids):
    """Persist one streamed tool call for the Runs audit view and return it.

    For gateway calls the result body and the real failure reason live in the
    GatewayFetch corpus (the event stream names the tool but omits its result),
    so the captured row is the source of truth — not the stream's error flag. A
    page miss (paywall/404) is now returned to the agent WITHOUT isError so it
    doesn't trip the circuit breaker, so the stream would call it a success;
    reading the row keeps the audit honest (the row carries the reason)."""
    duration = event.get("duration")
    tool = event.get("tool", "")
    raw_error = event.get("error")
    error_text = raw_error if isinstance(raw_error, str) else ""
    result_summary = ""

    row = _next_gateway_fetch(run, tool, seen_ids)
    if row is not None:
        if row.url:
            arguments = {**arguments, "url": row.url}
        if row.error:
            error_text = row.error
        else:
            result_summary = row.content

    # Error if the captured row (or, for non-gateway tools, the stream) says so.
    is_error = bool(error_text) or (bool(raw_error) and row is None)
    return ResearchAgentToolCall.objects.create(
        run=run,
        tool=tool,
        arguments=arguments,
        status=AgentToolCallStatus.ERROR if is_error else AgentToolCallStatus.SUCCESS,
        result_summary=result_summary,
        duration_ms=int(duration * 1000) if duration else None,
        result_tokens=_token_count(result_summary),
        error=error_text,
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


def _finalize_at_budget(hermes, run):
    """At the run budget: instead of stopping cold with nothing, ask the agent to
    write its final answer NOW from what it already gathered — no more searching or
    reading. Salvages a real result rather than failing empty. Best-effort."""
    try:
        started = hermes.start_run(
            input_text=(
                "You have reached this run's time/tool budget — do NOT search, "
                "read, or call any tool again. Using ONLY the sources you have "
                'already gathered in this session, return your final {"stagingItems": '
                "[...]} object now — a single JSON object, no prose, no markdown fences."
            ),
            instructions="",
            session_id=f"{run.session.hermes_session_id}-r{run.id}",
        )
    except Exception as exc:  # noqa: BLE001 - best-effort salvage
        logger.warning("Budget finalize could not start: %s", exc)
        return ""
    chunks = []
    try:
        for event in hermes.stream_events(started["run_id"]):
            if event.get("event") == "message.delta":
                chunks.append(event.get("delta", ""))
            elif event.get("event") == "run.completed":
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("Budget finalize stream failed: %s", exc)
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
            result_tokens=_token_count(r["error"] or r["raw"]),
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
    return text


def _stage_items(run, items, soul, version):
    """Persist Scout's candidates, coercing types at the DB boundary."""
    now = timezone.now()
    staged = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        # DB-boundary guard: a candidate with no title is malformed model
        # output — a template echo or salvage noise from a repaired envelope.
        # Persisting it floods the review inbox with empty cards, so drop it.
        title = str(item.get("title") or "").strip()
        if not title:
            logger.warning("Scout staging skipped a titleless item (run %s)", run.id)
            continue
        year = item.get("year")
        confidence = item.get("confidence")
        try:
            ResearchStagingItem.objects.create(
                project=run.project,
                run=run,
                title=title[:512],
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
    hermes = get_hermes_service(run.project)
    hermes.provision_soul(soul_content)

    _set_status(run, "Querying research tools…")
    scout_input = _scout_input(run, run.task, soul_content)

    _set_status(run, "Starting Scout…")
    quick = (run.selected_context or {}).get("depth") == "quick"
    instructions = build_scout_instructions(
        soul_content,
        # Upper bounds, not targets — the brief says stage what the evidence
        # justifies; 1 great source or 10 are both fine.
        max_candidates=3 if quick else 10,
        max_searches=2 if quick else 5,
    )
    if not _start_run(hermes, run, scout_input, instructions):
        return

    searches = 0
    seen_ids = set()  # GatewayFetch rows already claimed by this run's audit

    def on_tool(event, preview):
        nonlocal searches
        searches += 1
        # Only the real call preview — substituting the task text here would
        # mislead the Runs audit when Hermes sends none.
        _record_tool_call(run, event, {"query": preview}, seen_ids)
        _set_status(run, f"Searched {searches} source{'s' if searches != 1 else ''}…")

    streamed = _stream_run(hermes, run, "The Scout run was interrupted.", on_tool)
    if streamed is None:
        return
    output, stopped = streamed

    _set_status(run, "Evaluating findings…")
    items = parse_staging_items(output)
    if not items and stopped:
        # Hit the budget mid-work — don't end empty. Force one final synthesis
        # from what was already gathered (no further searching or reading).
        _set_status(run, "At the budget — writing up final findings…")
        items = parse_staging_items(_finalize_at_budget(hermes, run))
    elif not items and output.strip():
        _set_status(run, "Repairing the result format…")
        items = parse_staging_items(
            _reask_json(
                hermes, run, 'the {"stagingItems": [...]} object from your instructions'
            )
        )
    staged = _stage_items(run, items, soul, version)

    plural = "s" if staged != 1 else ""
    if stopped:
        # Budget hit: we forced a final synthesis above, so the run delivers what
        # it gathered rather than failing empty. Only a truly empty result fails.
        detail = (
            f"Reached the run budget ({stopped}); finalized with "
            f"{staged} finding{plural} from what was gathered."
            if staged
            else f"Reached the run budget ({stopped}); could not finalize a finding."
        )
        _finish(run, detail, hermes, failed=not staged)
    elif staged == 0:
        # Surface what happened — a deliberate empty envelope (no research
        # intent in the request) reads differently from an opaque zero.
        if '"stagingItems"' in output:
            detail = "Staged 0 findings — the request had nothing to research."
        else:
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
    hermes = get_hermes_service(run.project)
    hermes.provision_soul(soul_content)

    _set_status(run, "Reading the source…")
    if not _start_run(
        hermes, run, critic_input(item), build_critic_instructions(soul_content)
    ):
        return

    seen_ids = set()  # GatewayFetch rows already claimed by this run's audit

    def on_tool(event, preview):
        _record_tool_call(run, event, {"itemId": item.id, "query": preview}, seen_ids)
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
