"""
Web search source extractors for LLM providers.

This module provides classes to extract web search sources/citations from
streaming responses across different LLM providers (OpenAI, Claude, Gemini).

Each extractor follows a consistent interface:
- process_chunk(): Called for each streaming chunk
- get_sources(): Returns collected sources as a list of dicts
- clear(): Resets the extractor for reuse
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class WebSearchSource:
    """Standardized web search source data structure."""
    url: str
    title: str = ""
    cited_text: str = ""
    page_age: str = ""
    provider: str = ""

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary for database storage."""
        return {
            "url": self.url,
            "title": self.title,
            "cited_text": self.cited_text,
            "page_age": self.page_age,
            "provider": self.provider,
        }


class OpenAIWebSearchExtractor:
    """
    Extracts web search sources from OpenAI Responses API.

    OpenAI returns citations as 'annotations' within output_text content blocks.
    During streaming, annotations come via 'response.output_text.annotation.added'
    events and in the final 'response.completed' event.

    Each annotation contains:
    - type: "url_citation"
    - url: Source URL
    - title: Source title
    - start_index / end_index: Position in text
    """

    def __init__(self):
        self._sources: Dict[str, WebSearchSource] = {}  # Dedupe by URL

    def process_chunk(self, chunk: Any) -> None:
        """
        Process a streaming chunk to extract annotations.

        Args:
            chunk: OpenAI Responses API streaming chunk
        """
        if not hasattr(chunk, 'type'):
            return

        # Handle annotation added events during streaming
        if chunk.type == 'response.output_text.annotation.added':
            if hasattr(chunk, 'annotation'):
                self._extract_annotation(chunk.annotation)

        # Handle completed event with full response
        elif chunk.type == 'response.completed':
            if hasattr(chunk, 'response') and chunk.response:
                self._extract_from_completed_response(chunk.response)

    def _extract_annotation(self, annotation: Any) -> None:
        """Extract source from a single annotation object."""
        if not hasattr(annotation, 'type') or annotation.type != 'url_citation':
            return

        url = getattr(annotation, 'url', None)
        if not url:
            return

        # Deduplicate by URL
        if url not in self._sources:
            self._sources[url] = WebSearchSource(
                url=url,
                title=getattr(annotation, 'title', ''),
                provider='openai',
            )

    def _extract_from_completed_response(self, response: Any) -> None:
        """Extract all annotations from the completed response."""
        if not hasattr(response, 'output') or not response.output:
            return

        for item in response.output:
            if getattr(item, 'type', None) != 'message':
                continue

            if not hasattr(item, 'content') or not item.content:
                continue

            for content in item.content:
                if getattr(content, 'type', None) != 'output_text':
                    continue

                annotations = getattr(content, 'annotations', None)
                if annotations:
                    for annotation in annotations:
                        self._extract_annotation(annotation)

    def get_sources(self) -> List[Dict[str, str]]:
        """Return collected sources as list of dictionaries."""
        return [source.to_dict() for source in self._sources.values()]

    def clear(self) -> None:
        """Reset the extractor for reuse."""
        self._sources.clear()


class ClaudeWebSearchExtractor:
    """
    Extracts web search sources from Claude API.

    Claude returns sources in two places:
    1. web_search_tool_result blocks: Raw search results with url, title, page_age
    2. citations array in text blocks: What was actually cited with cited_text

    We extract from both to get the most complete picture.
    """

    def __init__(self):
        self._sources: Dict[str, WebSearchSource] = {}  # Dedupe by URL

    def process_event(self, event: Any) -> None:
        """
        Process a streaming event to extract sources.

        Args:
            event: Claude streaming event
        """
        if not hasattr(event, 'type'):
            return

        # Handle content block start (web_search_tool_result)
        if event.type == 'content_block_start':
            if hasattr(event, 'content_block'):
                cb = event.content_block
                if getattr(cb, 'type', None) == 'web_search_tool_result':
                    self._extract_from_tool_result(cb)

    def process_content_block(self, block: Any) -> None:
        """
        Process a content block from non-streaming response.

        Args:
            block: Claude content block
        """
        block_type = getattr(block, 'type', None)

        if block_type == 'web_search_tool_result':
            self._extract_from_tool_result(block)

        elif block_type == 'text':
            if hasattr(block, 'citations') and block.citations:
                for citation in block.citations:
                    self._extract_citation(citation)

    def _extract_from_tool_result(self, block: Any) -> None:
        """Extract sources from web_search_tool_result block."""
        if not hasattr(block, 'content') or not block.content:
            return

        for result in block.content:
            if getattr(result, 'type', None) != 'web_search_result':
                continue

            url = getattr(result, 'url', None)
            if not url:
                continue

            # Add or update source
            if url not in self._sources:
                self._sources[url] = WebSearchSource(
                    url=url,
                    title=getattr(result, 'title', ''),
                    page_age=getattr(result, 'page_age', ''),
                    provider='claude',
                )

    def _extract_citation(self, citation: Any) -> None:
        """Extract source from a citation in a text block."""
        if getattr(citation, 'type', None) != 'web_search_result_location':
            return

        url = getattr(citation, 'url', None)
        if not url:
            return

        cited_text = getattr(citation, 'cited_text', '')

        # Update existing source with cited_text or create new
        if url in self._sources:
            # Append cited text if not already present
            if cited_text and cited_text not in self._sources[url].cited_text:
                if self._sources[url].cited_text:
                    self._sources[url].cited_text += f" ... {cited_text}"
                else:
                    self._sources[url].cited_text = cited_text
        else:
            self._sources[url] = WebSearchSource(
                url=url,
                title=getattr(citation, 'title', ''),
                cited_text=cited_text,
                provider='claude',
            )

    def get_sources(self) -> List[Dict[str, str]]:
        """Return collected sources as list of dictionaries."""
        return [source.to_dict() for source in self._sources.values()]

    def clear(self) -> None:
        """Reset the extractor for reuse."""
        self._sources.clear()


class GeminiWebSearchExtractor:
    """
    Extracts web search sources from Gemini API grounding metadata.

    Gemini returns sources via grounding_metadata in response candidates:
    - grounding_chunks: Array of web sources with uri, title
    - grounding_supports: Maps text segments to sources (for context)

    Note: Gemini uses temporary redirect URLs that expire after a few days.
    """

    def __init__(self):
        self._sources: Dict[str, WebSearchSource] = {}  # Dedupe by URL

    def process_chunk(self, chunk: Any) -> None:
        """
        Process a streaming chunk to extract grounding metadata.

        Grounding metadata typically comes in the final chunk of the stream.

        Args:
            chunk: Gemini streaming chunk
        """
        if not hasattr(chunk, 'candidates') or not chunk.candidates:
            return

        candidate = chunk.candidates[0]
        if hasattr(candidate, 'grounding_metadata') and candidate.grounding_metadata:
            self._extract_from_metadata(candidate.grounding_metadata)

    def process_response(self, response: Any) -> None:
        """
        Process a non-streaming response to extract grounding metadata.

        Args:
            response: Gemini generate_content response
        """
        if not hasattr(response, 'candidates') or not response.candidates:
            return

        candidate = response.candidates[0]
        if hasattr(candidate, 'grounding_metadata') and candidate.grounding_metadata:
            self._extract_from_metadata(candidate.grounding_metadata)

    def _extract_from_metadata(self, metadata: Any) -> None:
        """Extract sources from grounding_metadata."""
        if not hasattr(metadata, 'grounding_chunks') or not metadata.grounding_chunks:
            return

        for chunk in metadata.grounding_chunks:
            if not hasattr(chunk, 'web') or not chunk.web:
                continue

            url = getattr(chunk.web, 'uri', None)
            if not url:
                continue

            # Deduplicate by URL
            if url not in self._sources:
                self._sources[url] = WebSearchSource(
                    url=url,
                    title=getattr(chunk.web, 'title', ''),
                    provider='gemini',
                )

    def get_sources(self) -> List[Dict[str, str]]:
        """Return collected sources as list of dictionaries."""
        return [source.to_dict() for source in self._sources.values()]

    def clear(self) -> None:
        """Reset the extractor for reuse."""
        self._sources.clear()
