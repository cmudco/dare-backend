"""
Node handler registry for managing and dispatching node handlers.

O(1) dict-based lookup replacing the previous O(n) list scan.
"""
import logging
from typing import Dict, Optional

from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
)
from workflows.handlers.file_handler import FileNodeHandler
from workflows.handlers.output_handler import OutputNodeHandler
from workflows.handlers.start_handler import StartNodeHandler
from workflows.handlers.step_handler import StepNodeHandler
from workflows.handlers.structured_output_handler import StructuredOutputNodeHandler
from workflows.handlers.utils.constants import NodeType


logger = logging.getLogger(__name__)


class NodeHandlerRegistry:
    """
    Registry for workflow node handlers.

    Stores handlers in a dict keyed by node type string for O(1) lookup.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, BaseNodeHandler] = {}
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Register the built-in node handlers."""
        self.register(NodeType.STEP, StepNodeHandler())
        self.register(NodeType.STRUCTURED_OUTPUT, StructuredOutputNodeHandler())
        self.register(NodeType.CHAT_OUTPUT, OutputNodeHandler())
        self.register(NodeType.START, StartNodeHandler())
        self.register(NodeType.FILE, FileNodeHandler())

        logger.info(f"Registered {len(self._handlers)} node handlers")

    def register(self, node_type: str, handler: BaseNodeHandler) -> None:
        """Register a handler for a specific node type."""
        self._handlers[node_type] = handler

    def get_handler(self, node_type: str) -> Optional[BaseNodeHandler]:
        """Get the handler for a node type. O(1) dict lookup."""
        handler = self._handlers.get(node_type)
        if not handler:
            logger.warning(f"No handler found for node type: {node_type}")
        return handler

    async def execute_node(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """Execute a node using its registered handler."""
        handler = self.get_handler(node.type)

        if not handler:
            return NodeExecutionResult(
                success=False,
                error=f"No handler found for node type: {node.type}",
            )

        return await handler.execute(node, context)


# Global registry instance
node_handler_registry = NodeHandlerRegistry()
