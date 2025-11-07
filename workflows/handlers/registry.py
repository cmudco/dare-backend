"""
Node handler registry for managing and dispatching node handlers.

This module provides a centralized registry for all node type handlers,
allowing for easy registration, lookup, and execution of handlers.
"""
import logging
from typing import List, Optional

from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
)
from workflows.handlers.conditional_handler import ConditionalNodeHandler
from workflows.handlers.output_handler import OutputNodeHandler
from workflows.handlers.start_handler import StartNodeHandler
from workflows.handlers.step_handler import StepNodeHandler


logger = logging.getLogger(__name__)


class NodeHandlerRegistry:
    """
    Registry for managing node type handlers.

    This class maintains a collection of node handlers and provides
    methods for registering new handlers, looking up handlers by node type,
    and executing nodes using the appropriate handler.
    """

    def __init__(self):
        """Initialize the registry with default handlers."""
        self._handlers: List[BaseNodeHandler] = []
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register the default node handlers."""
        self.register_handler(StepNodeHandler())
        self.register_handler(ConditionalNodeHandler())
        self.register_handler(OutputNodeHandler())
        self.register_handler(StartNodeHandler())

        logger.info("Registered default node handlers")

    def register_handler(self, handler: BaseNodeHandler):
        """
        Register a new node handler.

        Args:
            handler: The handler instance to register
        """
        self._handlers.append(handler)
        logger.debug(f"Registered handler: {handler.__class__.__name__}")

    def get_handler(self, node_type: str) -> Optional[BaseNodeHandler]:
        """
        Get the appropriate handler for a node type.

        Args:
            node_type: The type of node to find a handler for

        Returns:
            BaseNodeHandler if found, None otherwise
        """
        for handler in self._handlers:
            if handler.can_handle(node_type):
                return handler

        logger.warning(f"No handler found for node type: {node_type}")
        return None

    async def execute_node(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a node using the appropriate handler.

        Args:
            node: The execution node to process
            context: Execution context with previous results

        Returns:
            NodeExecutionResult with execution outcome
        """
        handler = self.get_handler(node.type)

        if not handler:
            logger.error(f"No handler found for node type: {node.type}")
            return NodeExecutionResult(
                success=False,
                error=f"No handler found for node type: {node.type}"
            )

        logger.debug(f"Executing node {node.id} with handler {handler.__class__.__name__}")

        return await handler.execute(node, context)


# Global registry instance
# This is the main entry point for executing workflow nodes
node_handler_registry = NodeHandlerRegistry()
