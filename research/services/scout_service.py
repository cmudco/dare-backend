"""
A Scout run: search, read and stage sources for one task from a chat thread.

The Scout stages each source with the ``stage_finding`` tool as it works and
reports what memory changed with ``report_learning``; its closing reply is a
short summary kept for the audit.
"""

import logging

from django.conf import settings

from mcp.services.mcp_gateway import gather_tool_results
from research.constants import AgentToolCallStatus
from research.models import ResearchAgentRun, ResearchAgentToolCall, ResearchStagingItem
from research.services.audit_service import token_count
from research.services.profile_service import hermes_for
from research.services.prompt_builder import scout_prompt
from research.services.run_service import (
    RunStream,
    cancelled_before_start,
    complete,
    set_detail,
    start,
)

logger = logging.getLogger(__name__)

_SEARCHES = ("web_search", "search_papers")


def _connected_tool_results(run):
    """Run the user's connected research tools on the task, as text for the input.

    DARE calls them itself so credentials stay here; each call is recorded on
    the run.
    """
    slugs = (run.selected_context or {}).get("tools") or run.project.enabled_tools
    results = gather_tool_results(run.user, slugs, run.task)
    for r in results:
        body = r["error"] or r["raw"]
        ResearchAgentToolCall.objects.create(
            run=run,
            tool=f"{r['slug']}__{r['tool']}",
            arguments={"query": run.task},
            status=(
                AgentToolCallStatus.ERROR if r["error"] else AgentToolCallStatus.SUCCESS
            ),
            result_summary=body,
            result_tokens=token_count(body),
            error=r["error"],
        )
    return "\n\n".join(
        f"### {r['slug']} · {r['tool']}\n{r['text']}"
        for r in results
        if r["text"] and not r["error"]
    )


def _empty_detail(run, counts, output):
    """Why a Scout staged nothing, in the user's terms."""
    set_aside = (run.learning or {}).get("setAside") or []
    refused = [
        r for r in set_aside if r.get("reason", "").startswith("Failed source check")
    ]
    if not counts["searched"] and not counts["read"]:
        return "Found nothing: the Scout never reached DARE's search tools."
    if refused:
        return f"Nothing staged: {len(refused)} source(s) failed DARE's source check."
    if set_aside:
        return f"Nothing staged: set aside {len(set_aside)} source(s) per your preferences."
    detail = (
        f"Searched {counts['searched']}x, read {counts['read']} pages, "
        "found nothing relevant."
    )
    snippet = " ".join((output or "").split())[:140]
    return f"{detail} Scout said: “{snippet}…”" if snippet else detail


def run_scout(run_id):
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    if run is None or cancelled_before_start(run):
        return
    hermes = hermes_for(run.project)
    set_detail(run, "Querying research tools…")
    prompt = scout_prompt(run, _connected_tool_results(run))
    set_detail(run, "Starting Scout…")
    if not start(run, hermes, prompt):
        return

    counts = {"searched": 0, "read": 0}

    def on_tool(call):
        name = call.tool.rsplit("__", 1)[-1]
        if name in _SEARCHES:
            counts["searched"] += 1
        elif name == "fetch_page":
            counts["read"] += 1
        staged = ResearchStagingItem.active_objects.filter(run=run).count()
        set_detail(
            run,
            f"{counts['searched']} searches · {counts['read']} pages read · "
            f"{staged} staged",
        )

    stream = RunStream(run, hermes, on_tool)
    for _ in stream:
        pass
    output = stream.result(wait_seconds=settings.HERMES_RUN_WAIT_SECONDS)
    if output is None:
        return
    staged = ResearchStagingItem.active_objects.filter(run=run).count()
    detail = (
        f"Staged {staged} finding{'s' if staged != 1 else ''}."
        if staged
        else _empty_detail(run, counts, output)
    )
    complete(run, hermes, detail, output)
