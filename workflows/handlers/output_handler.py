"""
Output node handler for workflow execution.

This handler stores and formats final output from workflow steps.
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
from workflows.models import ChatOutputNodeData


logger = logging.getLogger(__name__)


class OutputNodeHandler(BaseNodeHandler):
    """
    Handler for 'chatOutput' type nodes.

    This handler stores and formats the final output from its corresponding
    step node, making it available for the workflow execution results.
    """

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process 'chatOutput' nodes."""
        return node_type == 'chatOutput'

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute an output node by storing the result from its corresponding step.

        Args:
            node: The output node to execute
            context: Execution context containing the step result

        Returns:
            NodeExecutionResult with the formatted output
        """
        try:
            # Get output data from database
            output_data = await database_sync_to_async(
                lambda: node.db_node.data_object
            )()

            if not output_data or not isinstance(output_data, ChatOutputNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid output node data"
                )

            # Get the input from context (should be from corresponding step node)
            output_content = context.current_input or "No output from step"
            status = "completed" if context.current_input else "failed"
            error_message = "" if context.current_input else "No input received from step"

            # Update the output node data in database
            await database_sync_to_async(
                lambda: ChatOutputNodeData.objects.filter(id=output_data.id).update(
                    status=status,
                    response=output_content,
                    error=error_message
                )
            )()

            logger.info(f"Successfully updated output node {node.id}")

            return NodeExecutionResult(
                success=True,
                output=output_content,
                metadata={
                    'output_node_updated': True,
                    'status': status
                }
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            error_msg = f"{error_category}: {str(e)}"
            logger.error(
                f"{error_category} in output node {node.id} ({error_type}): {str(e)}",
                exc_info=True
            )

            return NodeExecutionResult(
                success=False,
                error=error_msg
            )
