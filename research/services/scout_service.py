"""
A Scout run: search, read and stage sources for one task from a chat thread.

The Scout searches with DARE's tools and the user's connected ones, stages
each source with ``stage_finding`` as it works and reports what memory changed
with ``report_learning``; its closing reply is a short summary for the audit.
"""

import logging

from django.conf import settings

from research.models import ResearchAgentRun, ResearchStagingItem
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
    set_detail(run, "Starting Scout…")
    if not start(run, hermes, scout_prompt(run)):
        return

    counts = {"searched": 0, "read": 0}

    def on_tool(call):
        name = call.tool.removeprefix("mcp__dare__")
        if name in _SEARCHES or "__" in name:
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
