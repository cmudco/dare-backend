"""
Workflow Web Search Source Service

Handles saving web search sources/citations from LLM responses to workflow steps.
Works in conjunction with the stream processors that extract sources during streaming.
Mirrors the conversation implementation for consistency.
"""

import logging
from typing import List, Dict

from channels.db import database_sync_to_async

from workflows.models import WorkflowRunStep, WorkflowStepWebSearchSource

logger = logging.getLogger(__name__)


class WorkflowWebSearchSourceService:
    """
    Service for managing web search sources in workflow steps.

    This service handles:
    - Saving extracted web search sources to the database
    - Deduplicating sources by URL
    - Cleaning up sources on step re-execution
    """

    @staticmethod
    @database_sync_to_async
    def save_sources(
        workflow_run_step: WorkflowRunStep,
        sources: List[Dict[str, str]],
    ) -> int:
        """
        Save web search sources to the database.

        Args:
            workflow_run_step: The WorkflowRunStep object to associate sources with
            sources: List of source dictionaries with keys:
                - url: Source URL (required)
                - title: Source title (optional)
                - cited_text: Quoted text from source (optional, Claude)
                - page_age: Page age info (optional, Claude)
                - provider: LLM provider name (optional)

        Returns:
            Number of sources saved
        """
        if not sources:
            return 0

        saved_count = 0
        seen_urls = set()

        for source_data in sources:
            url = source_data.get("url")
            if not url:
                continue

            # Skip duplicates within this batch
            if url in seen_urls:
                continue
            seen_urls.add(url)

            try:
                # Handle None values safely before slicing
                title = source_data.get("title") or ""
                cited_text = source_data.get("cited_text") or ""
                page_age = source_data.get("page_age") or ""
                provider = source_data.get("provider") or ""

                WorkflowStepWebSearchSource.active_objects.create(
                    workflow_run_step=workflow_run_step,
                    url=url,
                    title=title[:500],  # Truncate to field max
                    cited_text=cited_text,
                    page_age=page_age[:100],
                    provider=provider[:20],
                )
                saved_count += 1
            except Exception as e:
                logger.warning(f"Failed to save web search source {url}: {e}")
                continue

        if saved_count > 0:
            logger.info(
                f"Saved {saved_count} web search sources for workflow step {workflow_run_step.id}"
            )

        return saved_count

    @staticmethod
    @database_sync_to_async
    def delete_sources_for_step(workflow_run_step: WorkflowRunStep) -> int:
        """
        Delete all web search sources for a workflow step.

        Used when re-running a step to clear old sources.

        Args:
            workflow_run_step: The WorkflowRunStep object to clear sources for

        Returns:
            Number of sources deleted
        """
        deleted_count, _ = WorkflowStepWebSearchSource.active_objects.filter(
            workflow_run_step=workflow_run_step
        ).delete()
        if deleted_count > 0:
            logger.info(
                f"Deleted {deleted_count} web search sources for workflow step {workflow_run_step.id}"
            )
        return deleted_count
