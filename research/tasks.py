"""Queue entrypoints for research mode; each delegates to its service."""

import logging

import requests
from django_rq import job

from research.models import ResearchProject
from research.services.artifact_service import run_artifact
from research.services.chat_service import run_followup
from research.services.critic_service import run_critic
from research.services.profile_service import warm_profile
from research.services.run_service import notify_thread
from research.services.scout_service import run_scout

logger = logging.getLogger(__name__)


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
def warm_profile_job(project_id):
    project = ResearchProject.objects.filter(id=project_id).first()
    if project is None:
        return
    try:
        warm_profile(project)
    except requests.RequestException as exc:
        # Only an optimisation: the first real run connects the tools anyway.
        logger.warning("Warm-up for project %s failed: %s", project_id, exc)
