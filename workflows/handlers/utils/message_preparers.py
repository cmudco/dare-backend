"""
Message preparation utilities for workflow handlers.

This module extracts message building logic from handlers into reusable,
testable components following the pattern from LLM provider message formatters.
"""
import logging
from typing import Dict, List, Optional, Any

from channels.db import database_sync_to_async

from .constants import ErrorMessage, PromptTemplate
from .validation_helpers import MetadataValidator


logger = logging.getLogger(__name__)


# ==================== Message Preparer Base ====================

class MessagePreparer:
    """
    Base message preparer for workflow LLM interactions.

    Handles combination of prompts, previous results, and user inputs
    into properly formatted messages for LLM processing.
    """

    @staticmethod
    def combine_prompt_and_input(
        prompt_content: str,
        input_text: str,
        input_label: str = "Previous step result"
    ) -> str:
        """
        Combine prompt content with input text.

        Args:
            prompt_content: Base prompt text
            input_text: Input to combine
            input_label: Label for the input section

        Returns:
            Combined text with proper formatting
        """
        if prompt_content:
            return f"{prompt_content}\n\n{input_label}:\n{input_text}"
        return input_text

    @staticmethod
    def add_additional_input(base: str, text_input: str) -> str:
        """
        Add additional text input to base message.

        Args:
            base: Base message
            text_input: Additional text input

        Returns:
            Message with additional input if present, otherwise original base
        """
        if text_input and text_input.strip():
            return f"{base}\n\nAdditional input:\n{text_input.strip()}"
        return base

    @staticmethod
    def is_valid_result(result_data: Dict) -> bool:
        """
        Check if result data is valid and not skipped.

        Args:
            result_data: Result data dictionary from previous node

        Returns:
            True if result is valid and not skipped, False otherwise
        """
        if not result_data or not isinstance(result_data, dict):
            return False

        if not result_data.get('output'):
            return False

        metadata = result_data.get('metadata')
        return not MetadataValidator.is_skipped(metadata)

    @staticmethod
    def collect_previous_outputs(
        previous_results: Dict[str, Dict],
        include_node_ids: bool = True
    ) -> List[str]:
        """
        Collect valid outputs from previous node results.

        Args:
            previous_results: Dictionary of previous node results
            include_node_ids: Whether to include node IDs in output labels

        Returns:
            List of formatted output strings from previous nodes
        """
        outputs = []

        if not previous_results:
            return outputs

        for node_id, result_data in previous_results.items():
            if MessagePreparer.is_valid_result(result_data):
                output_text = result_data['output']
                if include_node_ids:
                    outputs.append(f"Result from {node_id}:\n{output_text}")
                else:
                    outputs.append(output_text)

        return outputs


# ==================== Step Message Preparer ====================

class StepMessagePreparer(MessagePreparer):
    """
    Message preparer specifically for step nodes.

    Handles the complex logic of combining prompts, previous results,
    and text inputs for step node LLM calls.
    """

    DEFAULT_TASK_MESSAGE = "Please complete the task described."

    @staticmethod
    async def prepare_message(
        prompt_content: str,
        text_input: str,
        previous_results: Dict[str, Dict]
        # REMOVED: current_input parameter
    ) -> str:
        """
        Prepare the message for LLM based on step configuration and context.

        This method combines:
        - Step's prompt content
        - Previous step results (from direct dependencies via edges)
        - Text input from step configuration

        Args:
            prompt_content: The prompt content from the step configuration
            text_input: Additional text input from step configuration
            previous_results: Dictionary of previous node results (edge-filtered)

        Returns:
            Formatted message ready for LLM processing
        """
        # Collect previous outputs from direct dependencies
        previous_outputs = StepMessagePreparer.collect_previous_outputs(
            previous_results,
            include_node_ids=True
        )

        # Build message based on available inputs
        if previous_outputs:
            if len(previous_outputs) == 1:
                # Single input - use traditional format
                # Extract just the output without the "Result from X:" prefix
                first_node_id = list(previous_results.keys())[0]
                combined_input = previous_outputs[0].replace(
                    f"Result from {first_node_id}:\n", ""
                )
                base = StepMessagePreparer.combine_prompt_and_input(
                    prompt_content,
                    combined_input,
                    "Previous step result"
                )
            else:
                # Multiple inputs - combine all results with IDs
                combined_input = "\n\n".join(previous_outputs)
                base = StepMessagePreparer.combine_prompt_and_input(
                    prompt_content,
                    combined_input,
                    "Results from previous steps"
                )

            # Add text input if present
            message = StepMessagePreparer.add_additional_input(base, text_input)

        else:
            # No previous input - use prompt and text input only
            base = prompt_content or StepMessagePreparer.DEFAULT_TASK_MESSAGE
            message = StepMessagePreparer.add_additional_input(base, text_input)

        return message


# ==================== Conditional Message Preparer ====================

class ConditionalMessagePreparer(MessagePreparer):
    """
    Message preparer for conditional nodes.

    Builds evaluation prompts for routing decisions.
    """

    @staticmethod
    def prepare_evaluation_prompt(
        input_text: str,
        available_routes: List[str],
        include_analysis: bool = True
    ) -> str:
        """
        Prepare an evaluation prompt for conditional routing.

        Args:
            input_text: The text to evaluate for routing
            available_routes: List of available route names
            include_analysis: Whether to request analysis in XML format

        Returns:
            Formatted evaluation prompt for LLM
        """
        # Format routes list
        routes_formatted = "\n".join(f"- {route}" for route in available_routes)

        if include_analysis:
            # XML format with analysis
            return PromptTemplate.CONDITIONAL_EVALUATION_WITH_ANALYSIS.format(
                routes=routes_formatted,
                input_text=input_text
            )
        else:
            # Simple format
            return PromptTemplate.CONDITIONAL_EVALUATION.format(
                routes=routes_formatted,
                input_text=input_text
            )

    @staticmethod
    def extract_single_input_from_results(
        previous_results: Dict[str, Dict]
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Extract single input from previous results for conditional evaluation.

        Conditional nodes require exactly one input to avoid ambiguity.

        Args:
            previous_results: Dictionary of previous node results

        Returns:
            Tuple of (success, error_message, input_text)
        """
        # Get non-skipped results
        valid_results = []
        for node_id, result_data in previous_results.items():
            if ConditionalMessagePreparer.is_valid_result(result_data):
                valid_results.append((node_id, result_data))

        # Validate single input requirement
        if len(valid_results) == 0:
            return False, ErrorMessage.MISSING_INPUT, None

        if len(valid_results) > 1:
            return False, ErrorMessage.AMBIGUOUS_INPUT, None

        # Extract input text
        input_text = valid_results[0][1].get('output')
        if not input_text:
            return False, "Input from previous node is empty", None

        return True, None, input_text


# ==================== Structured Output Message Preparer ====================

class StructuredOutputMessagePreparer(MessagePreparer):
    """
    Message preparer for structured output instructions.

    Adds routing instructions when LLM doesn't support native structured output.
    """

    @staticmethod
    def add_route_instruction_to_message(
        base_message: str,
        available_routes: List[str],
        default_route: Optional[str] = None
    ) -> str:
        """
        Add routing instruction to message for structured output.

        Used when provider doesn't support native structured output.

        Args:
            base_message: The base message to augment
            available_routes: List of available route values
            default_route: Default route if LLM uncertain

        Returns:
            Message with routing instructions appended
        """
        if default_route is None and available_routes:
            default_route = available_routes[0]

        route_values = ", ".join(f'"{route}"' for route in available_routes)

        instruction = PromptTemplate.STRUCTURED_OUTPUT_INSTRUCTION.format(
            route_values=route_values,
            default_route=default_route or "first option"
        )

        return f"{base_message}\n\n{instruction}"


# ==================== File Context Preparer ====================

class FileContextPreparer:
    """
    Preparer for file context in messages.

    Handles file content and embedding context extraction.
    """

    @staticmethod
    async def prepare_file_context(
        uploaded_files,
        similarity_threshold: float = 0.7,
        max_snippets: int = 3
    ) -> Optional[str]:
        """
        Prepare file context from uploaded files.

        Args:
            uploaded_files: QuerySet or list of uploaded file objects
            similarity_threshold: Minimum similarity for context inclusion
            max_snippets: Maximum number of context snippets

        Returns:
            Formatted file context string or None if no files
        """
        # TODO: Implement file context extraction
        # This would integrate with file processing service
        # For now, return None as placeholder
        logger.debug("File context preparation not yet implemented")
        return None


# ==================== Export All ====================

__all__ = [
    "MessagePreparer",
    "StepMessagePreparer",
    "ConditionalMessagePreparer",
    "StructuredOutputMessagePreparer",
    "FileContextPreparer",
]
