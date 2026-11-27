from django_rq import job

from knowledge_graph.services.relation_service import RelationBuildService

BUILD_TIMEOUT_SECONDS = 3600
FAILED_MESSAGE = (
    "This run stopped unexpectedly. Earlier relationships are unchanged. Try again."
)
NAME_CHECK_FAILED = (
    "Re-checking names stopped unexpectedly. Names are unchanged. Try again."
)


@job("default", timeout=BUILD_TIMEOUT_SECONDS)
def prepare_graph(graph_id: int) -> None:
    try:
        RelationBuildService.run_prepare(graph_id)
    except Exception:
        RelationBuildService.mark_failed(graph_id, FAILED_MESSAGE)
        raise


@job("default", timeout=BUILD_TIMEOUT_SECONDS)
def discover_graph_labels(graph_id: int) -> None:
    try:
        RelationBuildService.run_discovery(graph_id)
    except Exception:
        # Queue boundary: the page must not wait forever; RQ keeps the traceback.
        RelationBuildService.mark_failed(graph_id, FAILED_MESSAGE)
        raise


@job("default", timeout=BUILD_TIMEOUT_SECONDS)
def build_graph_relations(
    graph_id: int, new_only: bool = False, new_files_only: bool = False
) -> None:
    try:
        RelationBuildService.run_build(graph_id, new_only, new_files_only)
    except Exception:
        RelationBuildService.mark_failed(graph_id, FAILED_MESSAGE)
        raise


@job("default", timeout=BUILD_TIMEOUT_SECONDS)
def check_graph_names(graph_id: int, resting_status: str) -> None:
    try:
        RelationBuildService.run_name_check(graph_id, resting_status)
    except Exception:
        RelationBuildService.mark_failed(graph_id, NAME_CHECK_FAILED)
        raise
