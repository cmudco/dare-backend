"""
Base classes and utilities for workflow node handlers.

This module provides the abstract base class and shared data structures
used by all node type handlers.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async

from workflows.models import WorkflowNode, WorkflowRun, WorkflowRunStep
from core.services.llm_service import LLMService


logger = logging.getLogger(__name__)


def categorize_error(exception: Exception) -> tuple[str, str]:
    """
    Categorize exception into error type and category.

    Args:
        exception: The exception to categorize

    Returns:
        tuple: (error_category, error_type_name)
    """
    error_type = type(exception).__name__

    if isinstance(exception, (ValidationError, ValueError)):
        error_category = "Validation error"
    elif isinstance(exception, (ConnectionError, TimeoutError)):
        error_category = "Service error"
    else:
        error_category = "Unexpected error"

    return error_category, error_type


@dataclass
class ExecutionNode:
    """
    Simplified node representation for execution planning.

    Attributes:
        id: Unique node identifier
        type: Node type ('start', 'step', 'chatOutput', 'structuredOutput')
        step_number: Optional step ordering number
        db_node: Reference to the database WorkflowNode object
        next_node_id: ID of the next node in the flow
        output_node_id: ID of corresponding output node (for step nodes)
    """
    id: str
    type: str
    step_number: Optional[int]
    db_node: WorkflowNode
    next_node_id: Optional[str] = None
    output_node_id: Optional[str] = None


@dataclass
class NodeExecutionContext:
    """
    Context for node execution.

    Attributes:
        workflow_run: The current workflow run instance
        previous_results: Results from previously executed nodes (edge-based filtering)
        send_callback: Optional async callback for streaming progress updates to clients
        is_single_step_execution: True when executing a single step in manual mode (allows re-runs)
    """
    workflow_run: WorkflowRun
    previous_results: Dict[str, Any]
    send_callback: Optional[Any] = None  # Callable[[Dict], Awaitable[None]]
    is_single_step_execution: bool = False  # Manual mode single step execution
    # REMOVED: current_input (use previous_results with edge-based filtering)


@dataclass
class NodeExecutionResult:
    """
    Result of node execution.

    Attributes:
        success: Whether the execution succeeded
        output: The output produced by the node
        error: Error message if execution failed
        token_usage: LLM token usage statistics
        execution_time: Time taken to execute in seconds
        metadata: Additional metadata about the execution
    """
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    token_usage: Optional[Dict] = None
    execution_time: Optional[float] = None
    metadata: Optional[Dict] = None


class BaseNodeHandler(ABC):
    """
    Abstract base class for all node handlers.

    All node type handlers must inherit from this class and implement
    the execute() and can_handle() methods.
    """

    def __init__(self):
        """Initialize the handler with required services."""
        self.llm_service = LLMService()

    @abstractmethod
    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute the node with given context.

        Args:
            node: The execution node to process
            context: Execution context with previous results

        Returns:
            NodeExecutionResult with execution outcome
        """
        pass

    @abstractmethod
    def can_handle(self, node_type: str) -> bool:
        """
        Check if this handler can process the given node type.

        Args:
            node_type: The type of node to check

        Returns:
            bool: True if this handler can process the node type
        """
        pass

    async def _get_workflow_run_step(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode
    ) -> Optional[WorkflowRunStep]:
        """
        Get the WorkflowRunStep for this node if it exists.

        Args:
            workflow_run: The workflow run instance
            node: The execution node

        Returns:
            WorkflowRunStep if found, None otherwise
        """
        try:
            return await database_sync_to_async(
                lambda: WorkflowRunStep.objects.filter(
                    workflow_run=workflow_run,
                    step_node=node.db_node
                ).first()
            )()
        except Exception as e:
            logger.warning(f"Failed to get workflow run step: {e}")
            return None
