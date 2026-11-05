from django_rq import job

from knowledge_graph.services.relation_service import RelationBuildService

BUILD_TIMEOUT_SECONDS = 3600
FAILED_MESSAGE = "This run stopped unexpectedly. Try again."


@job("default", timeout=BUILD_TIMEOUT_SECONDS)
def discover_graph_labels(graph_id: int) -> None:
    try:
        RelationBuildService.run_discovery(graph_id)
    except Exception:
        # Queue boundary: the page must not wait forever; RQ keeps the traceback.
        RelationBuildService.mark_failed(graph_id, FAILED_MESSAGE)
        raise


@job("default", timeout=BUILD_TIMEOUT_SECONDS)
def build_graph_relations(graph_id: int) -> None:
    try:
        RelationBuildService.run_build(graph_id)
    except Exception:
        RelationBuildService.mark_failed(graph_id, FAILED_MESSAGE)
        raise
