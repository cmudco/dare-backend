import logging

from django.contrib.auth import get_user_model

from knowledge_graph.services.cluster_service import EntityClusterService

logger = logging.getLogger(__name__)


def sync_clusters_after_ingest(user_id: int) -> None:
    """Free name-rule clustering. A failure must not fail an ingest already published."""
    user = get_user_model().objects.get(pk=user_id)
    try:
        EntityClusterService.sync_user(user)
        EntityClusterService.refresh_labels(user)
    except Exception:
        # Ingest boundary: the document is already searchable, so clustering can retry.
        logger.warning("Entity clustering failed for user %s", user_id, exc_info=True)
