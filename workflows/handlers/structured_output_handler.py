"""
Structured output handler for workflow execution.

This module handles structured output processing, route resolution, and
response normalization for step nodes that use structured outputs.
"""
import logging
import re
from typing import Optional, List, Dict, Any, Tuple
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class StructuredOutputHandler:
    """
    Handler for structured output processing in workflow steps.

    This class encapsulates all logic related to:
    - Resolving allowed routes from StructuredOutput nodes
    - Building structured output instructions for LLMs
    - Normalizing and validating LLM responses against allowed routes
    - Providing fallback mechanisms when responses don't match expected routes
    """

    @staticmethod
    async def resolve_routes_for_step(
        workflow_run,
        step_node_id: str
    ) -> List[str]:
        """
        Resolve allowed routes from connected StructuredOutput node.

        This method traverses the workflow graph to find any StructuredOutput
        node that connects to the given step node, and extracts the defined routes.

        Args:
            workflow_run: The current workflow run
            step_node_id: The ID of the step node

        Returns:
            List of route names (strings)
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
                                return [
                                    str(r.get('name', '')).strip()
                                    for r in routes
                                    if r and r.get('name')
                                ]
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
    def build_route_instruction(allowed_routes: List[str]) -> str:
        """
        Build instruction text for LLM about route selection.

        Creates a clear, formatted instruction that tells the LLM exactly
        what routes are available and how to respond.

        Args:
            allowed_routes: List of valid route names

        Returns:
            Formatted instruction string
        """
        if not allowed_routes:
            return ""

        default_choice = allowed_routes[0]

        instruction = (
            "\n\nROUTE SELECTION INSTRUCTIONS:\n"
            f"Choose exactly one of: {', '.join(allowed_routes)}.\n"
            "Return only the exact value with no quotes, punctuation, or explanation.\n"
            f"If you are unsure or lack context, choose '{default_choice}'."
        )

        return instruction

    @staticmethod
    def normalize_route_response(
        raw_response: str,
        allowed_routes: List[str],
        step_node_id: str
    ) -> Tuple[str, str]:
        """
        Normalize LLM response to match one of the allowed routes.

        This method applies multiple strategies to match the LLM's response
        to an allowed route:
        1. Exact match
        2. Case-insensitive match
        3. First token match
        4. Fallback to default (first route)

        Args:
            raw_response: The raw response from the LLM
            allowed_routes: List of valid route names
            step_node_id: Step node ID for logging

        Returns:
            Tuple of (normalized_route, raw_response)
        """
        if not allowed_routes:
            logger.warning(f"No allowed routes provided for step {step_node_id}")
            return raw_response, raw_response

        # Clean and extract first line
        s = raw_response.strip().strip('"').strip("'")
        s = s.splitlines()[0].strip() if s else s

        # Strategy 1: Direct exact match
        if s in allowed_routes:
            return s, raw_response

        # Strategy 2: Case-insensitive match
        lower_map = {r.lower(): r for r in allowed_routes}
        if s.lower() in lower_map:
            return lower_map[s.lower()], raw_response

        # Strategy 3: Extract first token and try matching
        token = re.split(r"[^A-Za-z0-9_\-\.]+", s)[0] if s else ""
        if token in allowed_routes:
            return token, raw_response

        if token.lower() in lower_map:
            return lower_map[token.lower()], raw_response

        # Strategy 4: Fallback to first route
        default_route = allowed_routes[0]
        logger.warning(
            f"Step {step_node_id} returned non-matching structured output '{s}'; "
            f"defaulting to '{default_route}'"
        )

        return default_route, raw_response

    @staticmethod
    def build_structured_spec(allowed_routes: List[str]) -> Optional[Dict[str, Any]]:
        """
        Build structured output specification for LLM services.

        Creates a specification that can be used by LLM services that support
        structured outputs (e.g., OpenAI's structured outputs, Gemini's response schema).

        Args:
            allowed_routes: List of valid route names

        Returns:
            Structured spec dictionary or None if not applicable
        """
        if not allowed_routes:
            return None

        return {
            'type': 'enum_route',
            'field': 'route',
            'values': allowed_routes,
            'enforce': True,
        }

    @staticmethod
    def create_metadata_for_step(
        selected_route: str,
        raw_response: str,
        allowed_routes: List[str],
        use_structured: bool
    ) -> Dict[str, Any]:
        """
        Create metadata dictionary for workflow run step.

        Args:
            selected_route: The normalized selected route
            raw_response: The raw LLM response
            allowed_routes: List of valid routes
            use_structured: Whether structured output was used

        Returns:
            Metadata dictionary
        """
        metadata = {
            'selected_route': selected_route,
            'use_structured_output_node': use_structured,
        }

        # Only include raw response if it differs from selected route
        if raw_response != selected_route:
            metadata['raw_response'] = raw_response

        if allowed_routes:
            metadata['available_routes'] = allowed_routes

        return metadata

    @staticmethod
    def log_structured_output_debug(
        step_node_id: str,
        use_structured: bool,
        text_input_len: int,
        content_files_count: int,
        embedding_files_count: int
    ):
        """
        Log debug information for structured output configuration.

        Args:
            step_node_id: Step node ID
            use_structured: Whether structured output is enabled
            text_input_len: Length of text input
            content_files_count: Number of content files
            embedding_files_count: Number of embedding files
        """
        try:
            logger.debug(
                f"Step {step_node_id}: use_structured_output_node={use_structured}, "
                f"text_input_len={text_input_len}, "
                f"content_files={content_files_count}, "
                f"embedding_files={embedding_files_count}"
            )
        except Exception:
            # Don't break execution if debug logging fails
            pass
