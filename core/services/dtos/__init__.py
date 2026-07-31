"""DTOs for LLM Service layer."""

from .builder import LLMQueryRequestBuilder
from .context_dto import ContextConfig
from .dispatch_credentials_dto import ResolvedDispatchCredentials
from .generation_dto import GenerationConfig
from .llm_descriptor_dto import LLMDescriptor
from .media_dto import MediaConfig
from .parsed_document_dto import (BoundingBox, DocumentStructure,
                                  ParsedDocument, ParsedElement,
                                  text_only_document)
from .prepared_chat_dto import PreparedChat
from .request_dto import LLMQueryChunk, LLMQueryRequest
from .socratic_dto import SocraticConfig
from .stream_event_dto import LLMStreamEvent, StreamEventKind
from .tool_dto import ToolCallRequest, ToolCallResult, ToolLoopConfig
from .websocket_dto import BillingCheckResult, MessageFinalizationResult

__all__ = [
    "BoundingBox",
    "ContextConfig",
    "DocumentStructure",
    "GenerationConfig",
    "LLMDescriptor",
    "MediaConfig",
    "ParsedDocument",
    "ParsedElement",
    "text_only_document",
    "ResolvedDispatchCredentials",
    "SocraticConfig",
    "LLMQueryRequest",
    "LLMQueryChunk",
    "LLMQueryRequestBuilder",
    "LLMStreamEvent",
    "PreparedChat",
    "StreamEventKind",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolLoopConfig",
    "BillingCheckResult",
    "MessageFinalizationResult",
]
