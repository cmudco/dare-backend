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

from typing import Dict, Any


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
