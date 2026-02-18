"""
Route resolution utilities for workflow handlers.

This module consolidates structured output handling and routing logic,
providing clean interfaces for route resolution, normalization, and
structured output specification.

All modern LLM providers (OpenAI, Gemini, Claude) now support native
structured output, returning JSON responses.
"""
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
from channels.db import database_sync_to_async

from workflows.handlers.utils.constants import NodeType

from .constants import MetadataKey
from .validation_helpers import RouteValidator


logger = logging.getLogger(__name__)


# ==================== Route Resolver ====================

class RouteResolver:
    """
    Resolver for extracting and validating workflow routes.

    Handles route discovery from StructuredOutput nodes and validation
    of routing decisions.
    """

    @staticmethod
    async def resolve_routes_for_step(
        workflow_run,
        step_node_id: str
    ) -> List[str]:
        """
        Resolve allowed routes from connected StructuredOutput node.

        Traverses the workflow graph to find any StructuredOutput node
        that connects to the given step node and extracts defined routes.

        Args:
            workflow_run: The current workflow run
            step_node_id: The ID of the step node

        Returns:
            List of route names (strings), empty list if none found
        """
        def _resolve():
            try:
                workflow = workflow_run.workflow

                # Find structuredOutput node that connects to this step
                for edge in workflow.edges.all():
                    if edge.target == step_node_id:
                        src_node = workflow.nodes.filter(
                            node_id=edge.source,
                            node_type=NodeType.STRUCTURED_OUTPUT
                        ).first()

                        if src_node and src_node.data_object:
                            try:
                                routes = src_node.data_object.get_routes()
                                extracted_routes = [
                                    str(r.get('name', '')).strip()
                                    for r in routes
                                    if r and r.get('name')
                                ]

                                # Validate extracted routes
                                is_valid, errors = RouteValidator.validate_routes_list(
                                    extracted_routes
                                )
                                if not is_valid:
                                    logger.warning(
                                        f"Invalid routes in StructuredOutput node: {errors}"
                                    )
                                    return []

                                return extracted_routes

                            except Exception as e:
                                logger.warning(
                                    f"Failed to extract routes from StructuredOutput node: {e}"
                                )
                                return []

                return []

            except Exception as e:
                logger.error(f"Error resolving routes for step {step_node_id}: {e}")
                return []

        return await database_sync_to_async(_resolve)()

    @staticmethod
    async def resolve_routes_for_routing_node(
        workflow,
        routing_node_id: str
    ) -> List[str]:
        """
        Resolve available routes from a routing node.

        Extracts route names from outgoing edge handles.

        Args:
            workflow: The workflow instance
            routing_node_id: The routing node ID

        Returns:
            List of route names extracted from edge handles
        """
        def _resolve():
            try:
                routes = []

                # Find all outgoing edges from routing node
                for edge in workflow.edges.all():
                    if edge.source == routing_node_id and edge.source_handle:
                        # Extract route from handle (format: "output-<route_name>")
                        if edge.source_handle.startswith("output-"):
                            route = edge.source_handle[7:]  # Remove "output-" prefix
                            routes.append(route)

                return list(set(routes))  # Remove duplicates

            except Exception as e:
                logger.error(
                    f"Error resolving routes for routing node {routing_node_id}: {e}"
                )
                return []

        return await database_sync_to_async(_resolve)()


# ==================== Route Normalizer ====================

class RouteNormalizer:
    """
    Normalizer for LLM routing responses.

    All providers now support native structured output and return JSON.
    This normalizer provides fallback matching for edge cases.
    """

    @staticmethod
    def normalize_route_response(
        raw_response: str,
        allowed_routes: List[str],
        node_id: str,
        case_sensitive: bool = False
    ) -> Tuple[str, str]:
        """
        Normalize LLM response to match one of the allowed routes.

        Primary strategy is JSON extraction since all providers now return JSON
        with native structured outputs. Fallback strategies handle edge cases.

        Args:
            raw_response: The raw response from the LLM (typically JSON)
            allowed_routes: List of valid route names
            node_id: Node ID for logging
            case_sensitive: Whether to enforce case-sensitive matching

        Returns:
            Tuple of (normalized_route, raw_response)
        """
        if not allowed_routes:
            logger.warning(f"No allowed routes provided for node {node_id}")
            return raw_response, raw_response

        # Strategy 1: JSON extraction (primary - all providers return JSON)
        try:
            data = json.loads(raw_response)
            if isinstance(data, dict):
                # Primary field name is 'route'
                route_value = data.get('route')
                if route_value:
                    if route_value in allowed_routes:
                        logger.debug(f"Route '{route_value}' extracted from JSON")
                        return route_value, raw_response
                    # Try case-insensitive match
                    if not case_sensitive:
                        lower_map = {r.lower(): r for r in allowed_routes}
                        if str(route_value).lower() in lower_map:
                            matched_route = lower_map[str(route_value).lower()]
                            logger.debug(f"Route '{route_value}' matched case-insensitive to '{matched_route}'")
                            return matched_route, raw_response
        except (json.JSONDecodeError, ValueError):
            # Not JSON, continue with fallback strategies
            pass

        # Strategy 2: Direct match (fallback for simple string responses)
        cleaned = raw_response.strip().strip('"').strip("'")
        cleaned = cleaned.splitlines()[0].strip() if cleaned else cleaned

        if cleaned in allowed_routes:
            logger.debug(f"Route '{cleaned}' matched directly")
            return cleaned, raw_response

        # Try case-insensitive match
        if not case_sensitive:
            lower_map = {r.lower(): r for r in allowed_routes}
            if cleaned.lower() in lower_map:
                matched_route = lower_map[cleaned.lower()]
                logger.debug(f"Route '{cleaned}' matched case-insensitive to '{matched_route}'")
                return matched_route, raw_response

        # Strategy 3: Fallback to default (first route)
        default_route = allowed_routes[0]
        logger.warning(
            f"Node {node_id} returned non-matching response '{cleaned}'; "
            f"available routes: {allowed_routes}; defaulting to '{default_route}'"
        )

        return default_route, raw_response


# ==================== Structured Output Builder ====================

class StructuredOutputBuilder:
    """
    Builder for structured output specifications and metadata.

    Creates unified specifications that can be transformed for different providers.
    """

    @staticmethod
    def build_structured_spec(
        allowed_routes: List[str],
        field_name: str = "route",
        description: str = "Route selection decision",
        include_explanation: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Build unified structured output specification.

        Creates a specification that can be used by the schema transformer
        to generate provider-specific formats.

        Args:
            allowed_routes: List of valid route names
            field_name: Name of the field in output (default: "route")
            description: Description of the field
            include_explanation: Whether to include explanation field (default: True)

        Returns:
            Unified structured spec dictionary or None if not applicable
        """
        if not allowed_routes:
            logger.debug("No routes provided, returning None for structured spec")
            return None

        # Validate routes
        is_valid, errors = RouteValidator.validate_routes_list(allowed_routes)
        if not is_valid:
            logger.warning(f"Invalid routes for structured output: {errors}")
            return None

        if include_explanation:
            # Return object schema with route and explanation fields
            return {
                'type': 'object',
                'properties': {
                    field_name: {
                        'type': 'enum',
                        'values': allowed_routes,
                        'description': description
                    },
                    'explanation': {
                        'type': 'string',
                        'description': 'Required: 1-2 sentence analysis explaining why this route was selected based on the context, configuration, or routing criteria. Must not be empty.',
                        'minLength': 10  # Ensure non-empty explanation
                    }
                },
                'required': [field_name, 'explanation'],
                'enforce': True,
            }
        else:
            # Legacy format: simple enum (backward compatibility)
            return {
                'type': 'enum',
                'field': field_name,
                'values': allowed_routes,
                'description': description,
                'enforce': True,
            }

    @staticmethod
    def create_route_metadata(
        selected_route: str,
        raw_response: str,
        allowed_routes: List[str],
        use_structured: bool = False,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create metadata dictionary for routing decision.

        Args:
            selected_route: The normalized selected route
            raw_response: The raw LLM response
            allowed_routes: List of valid routes
            use_structured: Whether structured output was used
            additional_data: Optional additional metadata to include

        Returns:
            Metadata dictionary with routing information
        """
        metadata = {
            MetadataKey.SELECTED_ROUTE: selected_route,
            MetadataKey.AVAILABLE_ROUTES: allowed_routes,
        }

        # Only include raw response if it differs from selected route
        if raw_response != selected_route:
            metadata[MetadataKey.RAW_RESPONSE] = raw_response

        # Include structured output flag if provided
        if use_structured:
            metadata['use_structured_output_node'] = True

        # Merge additional data
        if additional_data:
            metadata.update(additional_data)

        return metadata


# ==================== Export All ====================

__all__ = [
    "RouteResolver",
    "RouteNormalizer",
    "StructuredOutputBuilder",
]
