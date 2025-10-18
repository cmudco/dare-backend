"""
Start node handler for workflow execution.

This handler initializes workflow execution and validates start node configuration.
"""
import logging
from channels.db import database_sync_to_async

from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)
from workflows.models import StartNodeData


logger = logging.getLogger(__name__)


class StartNodeHandler(BaseNodeHandler):
    """
    Handler for 'start' type nodes.

    This handler initializes workflow execution by validating the start node
    configuration and returning workflow metadata.
    """

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process 'start' nodes."""
        return node_type == 'start'

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute a start node by initializing workflow context.

        Args:
            node: The start node to execute
            context: Execution context (not used for start nodes)

        Returns:
            NodeExecutionResult with workflow initialization status
        """
        try:
            # Get start data from database
            start_data = await database_sync_to_async(
                lambda: node.db_node.data_object
            )()

            if not start_data or not isinstance(start_data, StartNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid start node data"
                )

            logger.info(
                f"Workflow '{start_data.title}' started in {start_data.mode} mode"
            )

            return NodeExecutionResult(
                success=True,
                output=f"Workflow '{start_data.title}' initialized",
                metadata={
                    'workflow_title': start_data.title,
                    'workflow_mode': start_data.mode,
                    'workflow_description': start_data.description
                }
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"
            logger.error(
                f"{error_category} in start node {node.id} ({error_type}): {str(e)}",
                exc_info=True
            )

            return NodeExecutionResult(
                success=False,
                error=error_msg
            )
