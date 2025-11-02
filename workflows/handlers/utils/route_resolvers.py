"""
Route resolution utilities for workflow handlers.

This module consolidates structured output handling and routing logic,
providing clean interfaces for route resolution, normalization, and
structured output specification following LLM provider utility patterns.
"""
import logging
import re
from typing import Optional, List, Dict, Any, Tuple
from channels.db import database_sync_to_async

from .constants import MetadataKey, ErrorMessage, XMLTag
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
                            node_type='structuredOutput'
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
    async def resolve_routes_for_conditional(
        workflow,
        conditional_node_id: str
    ) -> List[str]:
        """
        Resolve available routes from a conditional node.

        Extracts route names from outgoing edge handles.

        Args:
            workflow: The workflow instance
            conditional_node_id: The conditional node ID

        Returns:
            List of route names extracted from edge handles
        """
        def _resolve():
            try:
                routes = []

                # Find all outgoing edges from conditional node
                for edge in workflow.edges.all():
                    if edge.source == conditional_node_id and edge.source_handle:
                        # Extract route from handle (format: "output-<route_name>")
                        if edge.source_handle.startswith("output-"):
                            route = edge.source_handle[7:]  # Remove "output-" prefix
                            routes.append(route)

                return list(set(routes))  # Remove duplicates

            except Exception as e:
                logger.error(
                    f"Error resolving routes for conditional {conditional_node_id}: {e}"
                )
                return []

        return await database_sync_to_async(_resolve)()


# ==================== Route Normalizer ====================

class RouteNormalizer:
    """
    Normalizer for LLM routing responses.

    Provides multi-strategy matching to handle various LLM response formats
    and ensure routing decisions match available routes.
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

        Uses two strategies:
        1. XML extraction - Checks for common XML tags (route, decision, choice, selection)
        2. Fallback to default - Uses first route if no match found

        Args:
            raw_response: The raw response from the LLM
            allowed_routes: List of valid route names
            node_id: Node ID for logging
            case_sensitive: Whether to enforce case-sensitive matching

        Returns:
            Tuple of (normalized_route, raw_response)
        """
        if not allowed_routes:
            logger.warning(f"No allowed routes provided for node {node_id}")
            return raw_response, raw_response

        # Clean response - remove quotes and whitespace
        cleaned = raw_response.strip().strip('"').strip("'")
        cleaned = cleaned.splitlines()[0].strip() if cleaned else cleaned

        # Strategy 1: Direct match (for native structured outputs like Gemini/OpenAI)
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

        # Strategy 2: XML extraction (for Claude and other providers that wrap in XML)
        for tag_name in ['route', 'decision', 'choice', 'selection']:
            xml_content = XMLTag.extract_tag_content(raw_response, tag_name)
            if xml_content:
                # Try exact match
                if xml_content in allowed_routes:
                    logger.debug(f"Route '{xml_content}' extracted from <{tag_name}> tag")
                    return xml_content, raw_response
                # Try case-insensitive match
                if not case_sensitive:
                    lower_map = {r.lower(): r for r in allowed_routes}
                    if xml_content.lower() in lower_map:
                        matched_route = lower_map[xml_content.lower()]
                        logger.debug(f"Route '{xml_content}' from <{tag_name}> matched to '{matched_route}'")
                        return matched_route, raw_response

        # Strategy 3: Fallback to default (first route)
        default_route = allowed_routes[0]
        logger.warning(
            f"Node {node_id} returned non-matching response '{cleaned}'; "
            f"available routes: {allowed_routes}; defaulting to '{default_route}'"
        )

        return default_route, raw_response

    @staticmethod
    def extract_route_from_xml(
        xml_response: str,
        allowed_routes: List[str],
        node_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract routing decision and analysis from XML-formatted response.

        Supports both ConditionalNode (<decision>) and StructuredOutputNode (<route>) formats.

        Args:
            xml_response: XML-formatted LLM response
            allowed_routes: List of valid route names
            node_id: Node ID for logging

        Returns:
            Tuple of (decision, analysis) or (None, None) if extraction fails
        """
        try:
            # Extract decision tag - try both <decision> (ConditionalNode) and <route> (StructuredOutputNode)
            decision = XMLTag.extract_tag_content(xml_response, XMLTag.DECISION)
            if not decision:
                # Try <route> tag for StructuredOutputNode
                decision = XMLTag.extract_tag_content(xml_response, 'route')

            if not decision:
                logger.warning(f"No <{XMLTag.DECISION}> or <route> tag found in response")
                return None, None

            # Extract analysis tag (optional)
            analysis = XMLTag.extract_tag_content(xml_response, XMLTag.ANALYSIS)

            # Normalize the decision
            normalized_decision, _ = RouteNormalizer.normalize_route_response(
                decision,
                allowed_routes,
                node_id
            )

            return normalized_decision, analysis

        except Exception as e:
            logger.error(f"Error extracting route from XML: {e}")
            return None, None


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
        description: str = "Route selection decision"
    ) -> Optional[Dict[str, Any]]:
        """
        Build unified structured output specification.

        Creates a specification that can be used by the schema transformer
        to generate provider-specific formats.

        Args:
            allowed_routes: List of valid route names
            field_name: Name of the field in output (default: "route")
            description: Description of the field

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


# ==================== Route Instruction Builder ====================

class RouteInstructionBuilder:
    """
    Builder for route selection instructions.

    Creates clear, formatted instructions for LLMs about route selection.
    """

    @staticmethod
    def build_simple_instruction(
        allowed_routes: List[str],
        default_route: Optional[str] = None
    ) -> str:
        """
        Build route selection instruction with XML format (EXACT match to ConditionalNode).

        Uses EXACT same format as ConditionalNode (conditional_prompt_service.py).

        Args:
            allowed_routes: List of valid route names
            default_route: Default route if uncertain (uses first route if None)

        Returns:
            Formatted instruction string with XML format matching ConditionalNode
        """
        if not allowed_routes:
            return ""

        # Build route list in XML format EXACTLY like ConditionalNode does
        route_xml_elements = "\n".join([
            f'<route name="{route}">{route}</route>'
            for route in allowed_routes
        ])

        # EXACT same format as ConditionalNode (conditional_prompt_service.py lines 115-131)
        # ConditionalNode puts this AFTER the user prompt with "Based on the following input..."
        # StructuredOutputNode appends this AFTER the step execution
        instruction = f"""

Based on your response above, choose the most appropriate route.

<routes>
{route_xml_elements}
</routes>

Analyze your response carefully and respond in this EXACT format (do not deviate):
<analysis>
[Brief reasoning for your choice - 1-2 sentences]
</analysis>
<route>[EXACT route name from the routes listed above]</route>"""

        return instruction

    @staticmethod
    def build_xml_instruction(
        allowed_routes: List[str],
        include_analysis: bool = True
    ) -> str:
        """
        Build XML-formatted route selection instruction.

        Used for conditional nodes that need analysis.

        Args:
            allowed_routes: List of valid route names
            include_analysis: Whether to include analysis requirement

        Returns:
            Formatted XML instruction string
        """
        if not allowed_routes:
            return ""

        route_list = ", ".join(allowed_routes)

        if include_analysis:
            instruction = (
                "\n\nROUTE SELECTION INSTRUCTIONS:\n"
                f"Available routes: {route_list}\n\n"
                "Respond in the following XML format:\n"
                f"<{XMLTag.DECISION}>route_name</{XMLTag.DECISION}>\n"
                f"<{XMLTag.ANALYSIS}>Your reasoning for this decision</{XMLTag.ANALYSIS}>\n\n"
                "The route_name must exactly match one of the available routes."
            )
        else:
            instruction = (
                "\n\nROUTE SELECTION INSTRUCTIONS:\n"
                f"Available routes: {route_list}\n\n"
                "Respond in XML format:\n"
                f"<{XMLTag.DECISION}>route_name</{XMLTag.DECISION}>\n\n"
                "The route_name must exactly match one of the available routes."
            )

        return instruction


# ==================== Export All ====================

__all__ = [
    "RouteResolver",
    "RouteNormalizer",
    "StructuredOutputBuilder",
    "RouteInstructionBuilder",
]
