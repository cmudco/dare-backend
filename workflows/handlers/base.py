"""
Base classes and data structures for workflow node handlers.

Provides the abstract base class (BaseNodeHandler) and shared dataclasses
(ExecutionNode, NodeExecutionContext, NodeExecutionResult) used by all handlers.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, Optional

from channels.db import database_sync_to_async
from django.core.exceptions import ValidationError

from core.services.llm_service import LLMService
from workflows.models import WorkflowNode, WorkflowRun, WorkflowRunStep


logger = logging.getLogger(__name__)


def categorize_error(exception: Exception) -> tuple[str, str]:
    """Categorize exception into (error_category, error_type_name)."""
    error_type = type(exception).__name__

    if isinstance(exception, (ValidationError, ValueError)):
        return "Validation error", error_type
    if isinstance(exception, (ConnectionError, TimeoutError)):
        return "Service error", error_type
    return "Unexpected error", error_type


# Type alias for the async send callback used across all handlers
SendCallback = Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]]


@dataclass
class ExecutionNode:
    """Simplified node representation for execution planning."""
    id: str
    type: str
    label: Optional[str]
    db_node: WorkflowNode
    next_node_id: Optional[str] = None
    output_node_id: Optional[str] = None


@dataclass
class NodeExecutionContext:
    """Context passed to every handler's execute() method."""
    workflow_run: WorkflowRun
    previous_results: Dict[str, Any]
    send_callback: SendCallback = None
    is_single_step_execution: bool = False
    batch_file_id: Optional[int] = None
    is_start_connected: bool = False


@dataclass
class NodeExecutionResult:
    """Result returned by every handler's execute() method."""
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    token_usage: Optional[Dict] = None
    execution_time: Optional[float] = None
    metadata: Optional[Dict] = None


class BaseNodeHandler(ABC):
    """
    Abstract base class for all node handlers.

    Subclasses implement execute() and can_handle().
    """

    def __init__(self) -> None:
        self.llm_service = LLMService()

    @abstractmethod
    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """Execute the node and return a result."""
        pass

    @abstractmethod
    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process the given node type."""
        pass

    async def _get_workflow_run_step(
        self,
        workflow_run: WorkflowRun,
        node: ExecutionNode,
    ) -> Optional[WorkflowRunStep]:
        """Get the existing WorkflowRunStep for this node, or None."""
        return await database_sync_to_async(
            lambda: WorkflowRunStep.objects.filter(
                workflow_run=workflow_run,
                step_node=node.db_node,
            ).first()
        )()
