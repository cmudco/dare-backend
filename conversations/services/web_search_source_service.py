"""
Web Search Source Service

Handles saving web search sources/citations from LLM responses to the database.
Works in conjunction with the stream processors that extract sources during streaming.
"""

import logging
from typing import List, Dict, Any
from channels.db import database_sync_to_async

from conversations.models import Message, WebSearchSource

logger = logging.getLogger(__name__)


class WebSearchSourceService:
    """
    Service for managing web search sources.

    This service handles:
    - Saving extracted web search sources to the database
    - Deduplicating sources by URL
    - Cleaning up sources on message regeneration
    """

    @staticmethod
    @database_sync_to_async
    def save_sources(
        message: Message,
        sources: List[Dict[str, str]],
    ) -> int:
        """
        Save web search sources to the database.

        Args:
            message: The Message object to associate sources with
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

                WebSearchSource.active_objects.create(
                    message=message,
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
            logger.info(f"Saved {saved_count} web search sources for message {message.id}")

        return saved_count

    @staticmethod
    @database_sync_to_async
    def delete_sources_for_message(message: Message) -> int:
        """
        Delete all web search sources for a message.

        Used when regenerating a message to clear old sources.

        Args:
            message: The Message object to clear sources for

        Returns:
            Number of sources deleted
        """
        deleted_count, _ = WebSearchSource.active_objects.filter(message=message).delete()
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} web search sources for message {message.id}")
        return deleted_count
