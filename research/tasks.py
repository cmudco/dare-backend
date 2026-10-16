"""Queue entrypoints for research mode; each delegates to its service."""

from django_rq import job

from research.services.artifact_service import run_artifact
from research.services.chat_service import finish_turn, run_followup
from research.services.critic_service import run_critic
from research.services.run_service import notify_thread
from research.services.scout_service import run_scout


@job("default")
def run_scout_job(run_id):
    try:
        run_scout(run_id)
    finally:
        notify_thread(run_id)


@job("default")
def run_critic_job(run_id, item_id):
    run_critic(run_id, item_id)


@job("default")
def run_artifact_job(run_id):
    run_artifact(run_id)


@job("default")
def run_thread_followup_job(thread_id):
    run_followup(thread_id)


@job("default")
def finish_turn_job(run_id):
    finish_turn(run_id)
