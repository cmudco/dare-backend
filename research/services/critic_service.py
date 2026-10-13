"""
The Critic: pressure-tests one staged source against the user's standards and
attaches its verdict to the item's ``critic_metadata``.
"""

import logging

import django_rq
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from research.constants import AgentRole, AgentRunStatus, ResearchSessionMode
from research.models import (
    ResearchAgentRun,
    ResearchSession,
    ResearchStagingItem,
    SoulFile,
)
from research.services.json_reply import find_json_object
from research.services.profile_service import hermes_for
from research.services.prompt_builder import critic_prompt
from research.services.run_service import RunStream, complete, set_detail, start

logger = logging.getLogger(__name__)

VERDICTS = {"holds", "overstated", "unsupported", "inconclusive"}


def request_critic(item, user):
    """Create the Critic run for ``item`` and queue it."""
    project = item.project
    run = ResearchAgentRun.objects.create(
        session=ResearchSession.get_or_create_scout_session(project, user),
        project=project,
        user=user,
        role=AgentRole.CRITIC,
        mode=ResearchSessionMode.SCOUT,
        task=f"Pressure-test: {item.title}",
        status=AgentRunStatus.RUNNING,
        status_detail="Queued…",
        soul_file_version=SoulFile.current_label(project),
        allowed_tools=["web"],
        started_at=timezone.now(),
    )
    transaction.on_commit(
        lambda: django_rq.get_queue("default").enqueue(
            "research.tasks.run_critic_job", run.id, item.id
        )
    )
    return run


def parse_verdict(output):
    """The verdict object in the Critic's reply, or None."""
    data = find_json_object(output or "", "verdict")
    if data is None:
        logger.warning("Critic reply had no verdict object")
        return None
    verdict = str(data.get("verdict") or "").strip().lower()
    concerns = data.get("concerns")
    return {
        "verdict": verdict if verdict in VERDICTS else "inconclusive",
        "reasoning": str(data.get("reasoning") or ""),
        "concerns": (
            [str(c) for c in concerns if c] if isinstance(concerns, list) else []
        ),
    }


def run_critic(run_id, item_id):
    run = ResearchAgentRun.objects.filter(id=run_id).first()
    item = ResearchStagingItem.objects.filter(id=item_id).first()
    if run is None or item is None:
        logger.warning("Critic run %s / item %s not found", run_id, item_id)
        return
    hermes = hermes_for(run.project)
    set_detail(run, "Reading the source…")
    if not start(run, hermes, critic_prompt(item)):
        return
    stream = RunStream(
        run, hermes, lambda call: set_detail(run, "Assessing the source…")
    )
    for _ in stream:
        pass
    output = stream.result(wait_seconds=settings.HERMES_RUN_WAIT_SECONDS)
    if output is None:
        return
    verdict = parse_verdict(output)
    if verdict is None:
        complete(run, hermes, "Critic could not return a verdict.", output)
        return
    item.critic_metadata = {
        **verdict,
        "runId": run.id,
        "assessedAt": timezone.now().isoformat(),
    }
    item.save(update_fields=["critic_metadata", "updated_at"])
    complete(run, hermes, f"Critic: {verdict['verdict']}.", output)
