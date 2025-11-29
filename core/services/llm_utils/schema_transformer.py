"""
Schema transformer for unified structured output handling.

This module provides transformation between a unified schema format
and provider-specific structured output formats.
"""
import json
import logging
import re
from typing import Dict, Any, Optional, List

from google.genai import types
from conversations.constants import Provider


logger = logging.getLogger(__name__)


class SchemaTransformer:
    """
    Transforms unified schema definitions to provider-specific formats.

    Unified Schema Formats:

    1. Simple Enum (Legacy):
    {
        'type': 'enum',
        'field': 'route',
        'values': ['approve', 'reject', 'escalate'],
        'description': 'Routing decision',
        'enforce': True
    }

    2. Object with Explanation (Recommended):
    {
        'type': 'object',
        'properties': {
            'route': {
                'type': 'enum',
                'values': ['approve', 'reject', 'escalate'],
                'description': 'The selected route'
            },
            'explanation': {
                'type': 'string',
                'description': 'Brief explanation for the routing decision'
            }
        },
        'required': ['route', 'explanation'],
        'enforce': True
    }

    Provider-Specific Formats:
    - OpenAI: Uses response_format with json_schema and strict mode
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
        supports = provider in [Provider.OPENAI.value, Provider.GEMINI.value]
        logger.debug(
            f"[SchemaTransformer] Provider '{provider}' native structured output support: {supports}"
        )
        return supports

    @staticmethod
    def transform_for_openai(schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Transform unified schema to OpenAI's json_schema format.

        Supports both simple enum and object schemas.

        Args:
            schema: Unified schema definition

        Returns:
            OpenAI response_format configuration or None
        """
        logger.debug(f"[SchemaTransformer] transform_for_openai called with schema: {schema}")

        if not schema:
            logger.warning("[SchemaTransformer] No schema provided")
            return None

        schema_type = schema.get('type')

        # Handle object schema (with explanation)
        if schema_type == 'object':
            properties = schema.get('properties', {})
            required = schema.get('required', [])

            if not properties:
                logger.warning("[SchemaTransformer] Object schema has no properties")
                return None

            # Build OpenAI schema properties
            openai_properties = {}
            for prop_name, prop_def in properties.items():
                prop_type = prop_def.get('type')

                if prop_type == 'enum':
                    # Enum property (e.g., route)
                    openai_properties[prop_name] = {
                        "type": "string",
                        "enum": prop_def.get('values', []),
                        "description": prop_def.get('description', '')
                    }
                elif prop_type == 'string':
                    # String property (e.g., explanation)
                    openai_properties[prop_name] = {
                        "type": "string",
                        "description": prop_def.get('description', '')
                    }
                else:
                    logger.warning(f"[SchemaTransformer] Unsupported property type: {prop_type}")
                    continue

            json_schema = {
                "name": "RoutingDecision",
                "schema": {
                    "type": "object",
                    "properties": openai_properties,
                    "required": required,
                    "additionalProperties": False,
                },
                "strict": True,
            }

            result = {
                "type": "json_schema",
                "json_schema": json_schema
            }

            logger.info(
                f"[SchemaTransformer] OpenAI object transformation successful - "
                f"properties: {list(openai_properties.keys())}, required: {required}, strict: True"
            )

            return result

        # Handle legacy enum schema (backward compatibility)
        elif schema_type == 'enum':
            field_name = schema.get('field', 'route')
            enum_values = schema.get('values', [])
            description = schema.get('description', f'Select one of: {", ".join(enum_values)}')

            if not enum_values:
                logger.warning("[SchemaTransformer] No enum values provided for OpenAI structured output")
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

            result = {
                "type": "json_schema",
                "json_schema": json_schema
            }

            logger.info(
                f"[SchemaTransformer] OpenAI enum transformation successful - "
                f"field: {field_name}, values: {enum_values}, strict: True"
            )

            return result

        else:
            logger.warning(f"[SchemaTransformer] Unsupported schema type: {schema_type}")
            return None

    @staticmethod
    def transform_for_gemini(schema: Dict[str, Any]):
        """
        Transform unified schema to Gemini's response_schema format.

        Supports both simple enum and object schemas.

        Args:
            schema: Unified schema definition

        Returns:
            Tuple of (response_mime_type, response_schema) for Gemini
        """
        logger.debug(f"[SchemaTransformer] transform_for_gemini called with schema: {schema}")

        if not schema:
            logger.warning("[SchemaTransformer] No schema provided")
            return None, None

        schema_type = schema.get('type')

        # Handle object schema (with explanation)
        if schema_type == 'object':
            properties = schema.get('properties', {})
            required = schema.get('required', [])

            if not properties:
                logger.warning("[SchemaTransformer] Object schema has no properties")
                return None, None

            # Build Gemini schema properties
            gemini_properties = {}
            for prop_name, prop_def in properties.items():
                prop_type = prop_def.get('type')

                if prop_type == 'enum':
                    # Enum property (e.g., route)
                    gemini_properties[prop_name] = types.Schema(
                        type=types.Type.STRING,
                        enum=prop_def.get('values', [])
                    )
                elif prop_type == 'string':
                    # String property (e.g., explanation)
                    gemini_properties[prop_name] = types.Schema(
                        type=types.Type.STRING
                    )
                else:
                    logger.warning(f"[SchemaTransformer] Unsupported property type: {prop_type}")
                    continue

            response_schema = types.Schema(
                type=types.Type.OBJECT,
                properties=gemini_properties,
                required=required
            )

            logger.info(
                f"[SchemaTransformer] Gemini object transformation successful - "
                f"properties: {list(gemini_properties.keys())}, required: {required}, mime_type: application/json"
            )

            return 'application/json', response_schema

        # Handle legacy enum schema (backward compatibility)
        elif schema_type == 'enum':
            field_name = schema.get('field', 'route')
            enum_values = schema.get('values', [])

            if not enum_values:
                logger.warning("[SchemaTransformer] No enum values provided for Gemini structured output")
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

            logger.info(
                f"[SchemaTransformer] Gemini enum transformation successful - "
                f"field: {field_name}, values: {enum_values}, mime_type: application/json"
            )

            return 'application/json', response_schema

        else:
            logger.warning(f"[SchemaTransformer] Unsupported schema type: {schema_type}")
            return None, None

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
