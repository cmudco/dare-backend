"""
Artifact Planning Schemas for Structured Output

JSON schemas used to get reliable structured responses from LLMs
when planning artifact creation and modification.

COMPATIBILITY NOTES:
- OpenAI: Requires additionalProperties: false, all properties in required
- Claude: Requires additionalProperties: false, all properties in required
- Gemini: Does NOT accept additionalProperties field at all

Use get_artifact_plan_schema(provider) to get the right schema for each provider.
"""

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, Any, Optional, Union


# ========== Artifact Mode Enum ==========


class ArtifactMode(str, Enum):
    """Mode of artifact workflow execution."""

    CREATE = "create"  # New artifact creation
    RESUME = "resume"  # Resume paused artifact
    MODIFY = "modify"  # Append sections to existing artifact


# ========== LLM Response Schemas ==========


# Base schema that works for Gemini
_ARTIFACT_PLAN_SCHEMA_BASE: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "artifact_type": {
            "type": "string",
            "enum": ["document", "code", "diagram"],
            "description": "Type of artifact to create"
        },
        "title": {
            "type": "string",
            "description": "Clear, descriptive title for the artifact"
        },
        "outline": {
            "type": "string",
            "description": "Numbered sections outline. Format: '1. Section Title - Description\\n2. Another Section - Description'"
        },
        "estimated_sections": {
            "type": "integer",
            "description": "Number of sections in the outline (1-50)"
        }
    },
    "required": ["artifact_type", "title", "outline", "estimated_sections"]
}


_MODIFICATION_PLAN_SCHEMA_BASE: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "new_sections_outline": {
            "type": "string",
            "description": "Outline of NEW sections to append. Format: 'N. Section Title - Description' where N continues from existing sections"
        },
        "estimated_new_sections": {
            "type": "integer",
            "description": "Number of new sections to add (1-20)"
        }
    },
    "required": ["new_sections_outline", "estimated_new_sections"]
}


def get_artifact_plan_schema(provider: str = "openai") -> Dict[str, Any]:
    """
    Get the schema for artifact creation planning.
    
    Args:
        provider: LLM provider name ('openai', 'claude', 'gemini')
        
    Returns:
        Schema dict compatible with the specified provider
    """
    schema = _ARTIFACT_PLAN_SCHEMA_BASE.copy()
    schema["properties"] = _ARTIFACT_PLAN_SCHEMA_BASE["properties"].copy()
    
    # OpenAI and Claude require additionalProperties: false
    if provider.lower() in ["openai", "claude"]:
        schema["additionalProperties"] = False
    
    return schema


def get_modification_plan_schema(provider: str = "openai") -> Dict[str, Any]:
    """
    Get the schema for artifact modification planning.
    
    Args:
        provider: LLM provider name ('openai', 'claude', 'gemini')
        
    Returns:
        Schema dict compatible with the specified provider
    """
    schema = _MODIFICATION_PLAN_SCHEMA_BASE.copy()
    schema["properties"] = _MODIFICATION_PLAN_SCHEMA_BASE["properties"].copy()
    
    # OpenAI and Claude require additionalProperties: false
    if provider.lower() in ["openai", "claude"]:
        schema["additionalProperties"] = False
    
    return schema


# ========== Typed Artifact Events ==========


class ArtifactEventType(str, Enum):
    """Types of artifact events that flow through the graph."""
    INIT = "artifact_init"
    MODIFY_INIT = "artifact_modify_init"
    STREAM = "artifact_stream"
    PAUSE = "artifact_pause"
    COMPLETE = "artifact_complete"
    ERROR = "error"


@dataclass
class ArtifactInitEvent:
    """Event: New artifact created."""
    artifact_id: int
    title: str
    outline: str
    estimated_sections: int
    message_id: Optional[int] = None
    
    @property
    def type(self) -> str:
        return ArtifactEventType.INIT.value
    
    def to_websocket_message(self) -> Dict[str, Any]:
        """Convert to WebSocket message format."""
        msg = {
            "type": self.type,
            "artifactId": str(self.artifact_id),
            "title": self.title,
            "outline": self.outline,
            "estimatedSections": self.estimated_sections,
        }
        if self.message_id:
            msg["messageId"] = str(self.message_id)
        return msg


@dataclass
class ArtifactModifyInitEvent:
    """Event: Artifact modification started (new version created)."""
    artifact_id: int      # NEW artifact ID (not parent)
    parent_artifact_id: int
    artifact_group_id: int
    title: str
    outline: str  # New sections outline only
    full_outline: str  # Complete outline including parent's
    new_sections_count: int  # Number of NEW sections being added
    total_estimated_sections: int  # Total sections (parent's + new)
    current_section: int  # Inherited from parent (where we start from)
    existing_content: str  # Content from parent artifact
    version: int
    message_id: Optional[int] = None
    
    @property
    def type(self) -> str:
        return ArtifactEventType.MODIFY_INIT.value
    
    def to_websocket_message(self) -> Dict[str, Any]:
        """Convert to WebSocket message format."""
        msg = {
            "type": self.type,
            "artifactId": str(self.artifact_id),
            "parentArtifactId": str(self.parent_artifact_id),
            "artifactGroupId": str(self.artifact_group_id),
            "title": self.title,
            "outline": self.outline,
            "fullOutline": self.full_outline,
            "estimatedSections": self.new_sections_count,  # Legacy: new sections only
            "totalEstimatedSections": self.total_estimated_sections,
            "currentSection": self.current_section,
            "existingContent": self.existing_content,
            "newVersion": self.version,
        }
        if self.message_id:
            msg["messageId"] = str(self.message_id)
        return msg


@dataclass
class ArtifactStreamEvent:
    """Event: Section content streamed."""
    artifact_id: int
    section: int
    progress: float
    content: str
    
    @property
    def type(self) -> str:
        return ArtifactEventType.STREAM.value
    
    def to_websocket_message(self) -> Dict[str, Any]:
        """Convert to WebSocket message format."""
        return {
            "type": self.type,
            "artifactId": str(self.artifact_id),
            "section": self.section,
            "progress": self.progress,
            "chunk": self.content,
        }


@dataclass
class ArtifactPauseEvent:
    """Event: Artifact generation paused."""
    artifact_id: int
    current_section: int
    sections_remaining: int
    
    @property
    def type(self) -> str:
        return ArtifactEventType.PAUSE.value
    
    def to_websocket_message(self) -> Dict[str, Any]:
        """Convert to WebSocket message format."""
        return {
            "type": self.type,
            "artifactId": str(self.artifact_id),
            "currentSection": self.current_section,
            "sectionsRemaining": self.sections_remaining,
        }


@dataclass
class ArtifactCompleteEvent:
    """Event: Artifact generation completed."""
    artifact_id: int
    total_words: int
    
    @property
    def type(self) -> str:
        return ArtifactEventType.COMPLETE.value
    
    def to_websocket_message(self) -> Dict[str, Any]:
        """Convert to WebSocket message format."""
        return {
            "type": self.type,
            "artifactId": str(self.artifact_id),
            "totalWords": self.total_words,
        }


@dataclass
class ArtifactErrorEvent:
    """Event: Error occurred during generation."""
    error_message: str
    
    @property
    def type(self) -> str:
        return ArtifactEventType.ERROR.value
    
    def to_websocket_message(self) -> Dict[str, Any]:
        """Convert to WebSocket message format."""
        return {
            "type": self.type,
            "errorCode": "ARTIFACT_ERROR",
            "errorMessage": self.error_message,
        }


# Union type for all artifact events
ArtifactEvent = Union[
    ArtifactInitEvent,
    ArtifactModifyInitEvent,
    ArtifactStreamEvent,
    ArtifactPauseEvent,
    ArtifactCompleteEvent,
    ArtifactErrorEvent,
]
