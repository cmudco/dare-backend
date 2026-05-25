"""
LLM utilities package.

This package provides reusable utilities for LLM service implementations,
including message formatting, vision handling, error formatting, usage extraction,
stream processing, and web search tools.
"""

# Message formatters
from .message_formatters import (
    MessageFormatter,
    GeminiMessageFormatter,
    OpenAIMessageFormatter,
)

# Vision handlers
from .vision_handlers import (
    VisionHandler,
    OpenAIVisionHandler,
    ClaudeVisionHandler,
    GeminiVisionHandler,
)

# Error handlers
from .error_handlers import (
    BaseErrorHandler,
    OpenAIErrorHandler,
    ClaudeErrorHandler,
    GeminiErrorHandler,
)

# Usage extractors
from .usage_extractors import (
    UsageExtractor,
    OpenAIUsageExtractor,
    ClaudeUsageExtractor,
    GeminiUsageExtractor,
)

# Stream processors
from .stream_processors import (
    OpenAIStreamProcessor,
    ClaudeStreamProcessor,
    GeminiStreamProcessor,
    StreamAggregator,
)

# Web search tools
from .web_search_tools import (
    WebSearchTools,
    OpenAIWebSearchTools,
    ClaudeWebSearchTools,
    ClaudeWebFetchTools,
    GeminiWebSearchTools,
)

# Web search source extractors
from .web_search_extractors import (
    WebSearchSource,
    OpenAIWebSearchExtractor,
    ClaudeWebSearchExtractor,
    GeminiWebSearchExtractor,
)

# Artifact tools
from .artifact_tools import ArtifactTools

# Schema transformer
from .schema_transformer import SchemaTransformer

__all__ = [
    # Message formatters
    "MessageFormatter",
    "GeminiMessageFormatter",
    "OpenAIMessageFormatter",
    # Vision handlers
    "VisionHandler",
    "OpenAIVisionHandler",
    "ClaudeVisionHandler",
    "GeminiVisionHandler",
    # Error handlers
    "BaseErrorHandler",
    "OpenAIErrorHandler",
    "ClaudeErrorHandler",
    "GeminiErrorHandler",
    # Usage extractors
    "UsageExtractor",
    "OpenAIUsageExtractor",
    "ClaudeUsageExtractor",
    "GeminiUsageExtractor",
    # Stream processors
    "OpenAIStreamProcessor",
    "ClaudeStreamProcessor",
    "GeminiStreamProcessor",
    "StreamAggregator",
    # Web search tools
    "WebSearchTools",
    "OpenAIWebSearchTools",
    "ClaudeWebSearchTools",
    "ClaudeWebFetchTools",
    "GeminiWebSearchTools",
    # Web search source extractors
    "WebSearchSource",
    "OpenAIWebSearchExtractor",
    "ClaudeWebSearchExtractor",
    "GeminiWebSearchExtractor",
    # Artifact tools
    "ArtifactTools",
    # Schema transformer
    "SchemaTransformer",
]
