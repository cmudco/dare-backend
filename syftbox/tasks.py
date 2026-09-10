import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django_rq import enqueue, job

from core.storage.constants import StorageBackendChoice
from syftbox.services import syftbox_datasite_sync_service

logger = logging.getLogger(__name__)


@job
def sync_user_datasite(user_id: int) -> dict:
    """
    Run a single datasite sync cycle for one user.

    Intended to be enqueued manually first, then reused by a scheduler.
    """
    if not settings.SYFTBOX.get("ENABLED", False):
        logger.info(
            "Skipping datasite poll for user_id=%s: SyftBox sync is disabled.",
            user_id,
        )
        return {
            "status": "skipped",
            "user_id": user_id,
            "reason": "syftbox_disabled",
        }

    user_model = get_user_model()

    try:
        user = user_model.active_objects.get(id=user_id)
    except user_model.DoesNotExist:
        logger.warning("Skipping datasite poll: user %s does not exist.", user_id)
        return {
            "status": "skipped",
            "user_id": user_id,
            "reason": "user_not_found",
        }

    service = syftbox_datasite_sync_service.SyftBoxDatasitePollService()
    result = service.sync_user_datasite(user)

    payload = {
        "status": "ok",
        "user_id": user_id,
        "total_remote": result.total_remote,
        "created": result.created,
        "updated": result.updated,
        "kept": result.kept,
        "deleted": result.deleted,
        "failed": result.failed,
        "errors": result.errors,
    }

    logger.info("Completed datasite poll for user_id=%s: %s", user_id, payload)
    return payload


@job
def sync_syftbox_datasites() -> dict:
    """Enqueue one datasite sync job per eligible SyftBox user."""
    if not settings.SYFTBOX.get("ENABLED", False):
        payload = {
            "status": "skipped",
            "reason": "syftbox_disabled",
            "eligible_users": 0,
            "enqueued_jobs": 0,
        }
        logger.info("Skipping SyftBox datasite sync dispatch: sync is disabled.")
        return payload

    user_model = get_user_model()
    users = (
        user_model.active_objects.filter(storage_backend=StorageBackendChoice.SYFTBOX)
        .exclude(Q(syftbox_access_token__isnull=True) | Q(syftbox_access_token=""))
        .only("id", "email")
    )

    enqueued = 0
    for user in users.iterator():
        enqueue(sync_user_datasite, user.id)
        enqueued += 1

    payload = {
        "status": "ok",
        "eligible_users": users.count(),
        "enqueued_jobs": enqueued,
    }
    logger.info("Enqueued datasite sync jobs: %s", payload)
    return payload
