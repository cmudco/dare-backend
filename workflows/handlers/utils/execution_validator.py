"""
Execution-time validation for workflow nodes.

This module provides validation that runs BEFORE workflow execution,
ensuring all required data is present for successful execution.
"""
import logging
from typing import List, Tuple

from workflows.handlers.utils.constants import NodeType
from workflows.models import (
    Workflow, WorkflowNode, StepNodeData,
    StructuredOutputNodeData
)

logger = logging.getLogger(__name__)


class ExecutionValidator:
    """
    Validates workflow nodes before execution.

    This validator ensures that all execution-critical data is present
    before attempting to run a workflow, providing clear error messages.
    """

    @staticmethod
    def validate_workflow_for_execution(workflow: Workflow) -> Tuple[bool, List[str]]:
        """
        Validate entire workflow is ready for execution.

        Args:
            workflow: The workflow to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        logger.info(f"[ExecutionValidator] Validating workflow {workflow.id} for execution")
        errors = []

        # Get all nodes
        step_nodes = workflow.nodes.filter(node_type=NodeType.STEP)
        structured_output_nodes = workflow.nodes.filter(node_type=NodeType.STRUCTURED_OUTPUT)

        logger.info(f"[ExecutionValidator] Found {step_nodes.count()} step nodes, "
                   f"{structured_output_nodes.count()} structured output nodes")

        # Validate step nodes
        for node in step_nodes:
            node_errors = ExecutionValidator._validate_step_node(node)
            if node_errors:
                logger.warning(f"[ExecutionValidator] Step node {node.node_id} has errors: {node_errors}")
            errors.extend(node_errors)

        # Validate structured output nodes
        for node in structured_output_nodes:
            node_errors = ExecutionValidator._validate_structured_output_node(node)
            if node_errors:
                logger.warning(f"[ExecutionValidator] Structured output node {node.node_id} has errors: {node_errors}")
            errors.extend(node_errors)

        is_valid = len(errors) == 0
        logger.info(f"[ExecutionValidator] Validation complete - is_valid={is_valid}, error_count={len(errors)}")
        if errors:
            logger.info(f"[ExecutionValidator] Errors: {errors}")

        return (is_valid, errors)

    @staticmethod
    def _validate_step_node(node: WorkflowNode) -> List[str]:
        """
        Validate a step node for execution.

        Required:
        - prompt must be set
        - llm must be set

        Args:
            node: The step node to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        step_data = node.data_object

        logger.info(f"[ExecutionValidator] Validating step node {node.node_id}")
        logger.info(f"[ExecutionValidator] Step data type: {type(step_data)}")

        if not isinstance(step_data, StepNodeData):
            errors.append(f"Step node {node.node_id}: Invalid data type")
            return errors

        logger.info(f"[ExecutionValidator] Step {step_data.step_number} - "
                   f"has_prompt={step_data.prompt is not None}, "
                   f"has_llm={step_data.llm is not None}, "
                   f"prompt_id={step_data.prompt_id if hasattr(step_data, 'prompt_id') else 'N/A'}, "
                   f"llm_id={step_data.llm_id if hasattr(step_data, 'llm_id') else 'N/A'}")

        # Validate prompt (REQUIRED)
        if not step_data.prompt:
            logger.warning(f"[ExecutionValidator] Step node {node.node_id}: Missing prompt")
            errors.append(
                f"Step {step_data.step_number}: Missing required prompt. Please select a prompt before running."
            )

        # Validate LLM (REQUIRED)
        if not step_data.llm:
            logger.warning(f"[ExecutionValidator] Step node {node.node_id}: Missing LLM")
            errors.append(
                f"Step {step_data.step_number}: Missing required LLM. Please select an LLM before running."
            )

        return errors

    @staticmethod
    def _validate_structured_output_node(node: WorkflowNode) -> List[str]:
        """
        Validate a structured output node for execution.

        Required:
        - Either prompt OR text_input must be set (at least one)
        - llm must be set
        - routes must have at least 2 entries

        Args:
            node: The structured output node to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        struct_data = node.data_object

        if not isinstance(struct_data, StructuredOutputNodeData):
            errors.append(f"Structured output node {node.node_id}: Invalid data type")
            return errors

        step_number = getattr(struct_data, 'step_number', None)
        node_label = f"Structured Output {step_number}" if step_number else "Structured Output node"

        # Validate prompt OR text_input (at least one REQUIRED)
        has_prompt = struct_data.prompt is not None
        has_text_input = bool(struct_data.text_input and struct_data.text_input.strip())

        if not has_prompt and not has_text_input:
            errors.append(
                f"{node_label}: Missing required input. Please provide either a prompt or text input before running."
            )

        # Validate LLM (REQUIRED)
        if not struct_data.llm:
            errors.append(
                f"{node_label}: Missing required LLM selection"
            )

        # Validate routes (REQUIRED, minimum 2)
        routes = struct_data.get_routes()
        if len(routes) < 2:
            errors.append(
                f"{node_label}: Must have at least 2 routes defined. Please configure routes before running."
            )

        return errors


__all__ = ["ExecutionValidator"]
