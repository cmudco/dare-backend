"""
Schema transformer for unified structured output handling.

This module provides transformation between a unified schema format
and provider-specific structured output formats.
"""
import logging
from typing import Dict, Any, Optional, List
from conversations.constants import Provider


logger = logging.getLogger(__name__)


class SchemaTransformer:
    """
    Transforms unified schema definitions to provider-specific formats.
    
    Unified Schema Format:
    {
        'type': 'enum',  # Currently only 'enum' is supported
        'field': 'route',  # Field name in the output
        'values': ['approve', 'reject', 'escalate'],  # Allowed values
        'description': 'Routing decision',  # Optional description
        'enforce': True  # Whether to enforce strict validation
    }
    
    Provider-Specific Formats:
    - OpenAI: Uses response_format with json_schema
    - Gemini: Uses response_schema with type definitions
    - Claude: No native support, returns instructions for prompt engineering
    """

    @staticmethod
    def supports_native_structured_output(provider: str) -> bool:
        """
        Check if provider supports native structured outputs.
        
        Args:
            provider: Provider name (openai, claude, gemini, etc.)
        
        Returns:
            bool: True if provider has native structured output support
        """
        return provider in [Provider.OPENAI.value, Provider.GEMINI.value]

    @staticmethod
    def transform_for_openai(schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform unified schema to OpenAI's json_schema format.
        
        Args:
            schema: Unified schema definition
        
        Returns:
            OpenAI response_format configuration or None
        """
        if not schema or schema.get('type') != 'enum':
            return None

        field_name = schema.get('field', 'route')
        enum_values = schema.get('values', [])
        description = schema.get('description', f'Select one of: {", ".join(enum_values)}')

        if not enum_values:
            logger.warning("No enum values provided for OpenAI structured output")
            return None

        # OpenAI expects a json_schema with strict mode
        json_schema = {
            "name": f"{field_name.capitalize()}Selection",
            "schema": {
                "type": "object",
                "properties": {
                    field_name: {
                        "type": "string",
                        "enum": enum_values,
                        "description": description
                    }
                },
                "required": [field_name],
                "additionalProperties": False,
            },
            "strict": True,
        }

        return {
            "type": "json_schema",
            "json_schema": json_schema
        }

    @staticmethod
    def transform_for_gemini(schema: Dict[str, Any]):
        """
        Transform unified schema to Gemini's response_schema format.
        
        Args:
            schema: Unified schema definition
        
        Returns:
            Tuple of (response_mime_type, response_schema) for Gemini
        """
        from google.genai import types
        
        if not schema or schema.get('type') != 'enum':
            return None, None

        field_name = schema.get('field', 'route')
        enum_values = schema.get('values', [])

        if not enum_values:
            logger.warning("No enum values provided for Gemini structured output")
            return None, None

        # Gemini expects response_schema with type definitions
        response_schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                field_name: types.Schema(
                    type=types.Type.STRING,
                    enum=enum_values
                )
            },
            required=[field_name]
        )

        return 'application/json', response_schema

    @staticmethod
    def transform_for_claude(schema: Dict[str, Any]) -> Optional[str]:
        """
        Transform unified schema to Claude-compatible prompt instructions.
        
        Claude doesn't support native structured outputs, so we generate
        instructions for XML-based prompt engineering.
        
        Args:
            schema: Unified schema definition
        
        Returns:
            Instruction string to append to prompt, or None
        """
        if not schema or schema.get('type') != 'enum':
            return None

        field_name = schema.get('field', 'route')
        enum_values = schema.get('values', [])
        description = schema.get('description', 'Selection')

        if not enum_values:
            logger.warning("No enum values provided for Claude structured output")
            return None

        default_value = enum_values[0]

        # Claude instruction format (XML-based)
        instruction = (
            f"\n\n{description.upper()} INSTRUCTIONS:\n"
            f"You must respond with exactly one of these values: {', '.join(enum_values)}.\n"
            f"Format your response as: <{field_name}>your_choice</{field_name}>\n"
            f"Choose only from the provided options. If unsure, select '{default_value}'.\n"
            f"Do not include any other text in your response."
        )

        return instruction

    @staticmethod
    def extract_value_from_response(
        response: str,
        schema: Dict[str, Any],
        provider: str
    ) -> str:
        """
        Extract the structured value from a provider's response.
        
        Args:
            response: Raw response from LLM
            schema: Original unified schema
            provider: Provider name
        
        Returns:
            Extracted value (or fallback to first valid value)
        """
        import json
        import re
        
        field_name = schema.get('field', 'route')
        allowed_values = schema.get('values', [])
        
        if not allowed_values:
            return response.strip()

        # For OpenAI and Gemini (native structured outputs), parse JSON
        if provider in [Provider.OPENAI.value, Provider.GEMINI.value]:
            try:
                data = json.loads(response)
                value = data.get(field_name)
                if value in allowed_values:
                    return value
            except (json.JSONDecodeError, AttributeError):
                logger.warning(f"Failed to parse JSON from {provider} response")

        # For Claude or fallback, try XML extraction
        xml_match = re.search(rf'<{field_name}>(.+?)</{field_name}>', response, re.DOTALL)
        if xml_match:
            value = xml_match.group(1).strip()
            if value in allowed_values:
                return value

        # Last resort: normalize response with fuzzy matching
        return SchemaTransformer._normalize_with_fuzzy_match(
            response, allowed_values
        )

    @staticmethod
    def _normalize_with_fuzzy_match(response: str, allowed_values: List[str]) -> str:
        """
        Attempt to match response to allowed values using various strategies.
        
        Args:
            response: Raw response text
            allowed_values: List of valid values
        
        Returns:
            Best matched value or first value as fallback
        """
        # Clean response
        cleaned = response.strip().strip('"').strip("'")
        cleaned = cleaned.splitlines()[0].strip() if cleaned else cleaned

        # Strategy 1: Direct exact match
        if cleaned in allowed_values:
            return cleaned

        # Strategy 2: Case-insensitive match
        lower_map = {v.lower(): v for v in allowed_values}
        if cleaned.lower() in lower_map:
            return lower_map[cleaned.lower()]

        # Strategy 3: Extract first token
        import re
        tokens = re.split(r"[^A-Za-z0-9_\-\.]+", cleaned)
        for token in tokens:
            if token in allowed_values:
                return token
            if token.lower() in lower_map:
                return lower_map[token.lower()]

        # Fallback to first value
        default_value = allowed_values[0]
        logger.warning(
            f"Could not match '{cleaned}' to allowed values {allowed_values}; "
            f"defaulting to '{default_value}'"
        )
        return default_value
