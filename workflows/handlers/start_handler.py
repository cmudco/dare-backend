"""
Start node handler for workflow execution.

This handler initializes workflow execution and validates start node configuration.
Enhanced to support start node chaining by extracting input from previous workflow chains.
"""
import logging
from typing import Optional
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
    configuration and returning workflow metadata. Supports start node chaining
    by extracting input from previous workflow chains (e.g., Chat Output nodes).
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

        If this start node has incoming connections from a previous workflow chain
        (e.g., from a Chat Output node), the output from that chain is extracted
        and passed as output for use by downstream nodes in this chain.

        Args:
            node: The start node to execute
            context: Execution context containing previous node results

        Returns:
            NodeExecutionResult with workflow initialization status and optional chain input
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

            # Check if this start node receives input from a previous chain
            chain_input = await self._extract_chain_input(node, context)

            if chain_input:
                logger.info(
                    f"Start node '{start_data.title}' received chain input "
                    f"({len(chain_input)} chars)"
                )

                return NodeExecutionResult(
                    success=True,
                    output=chain_input,  # Pass chain input to downstream nodes
                    metadata={
                        'workflow_title': start_data.title,
                        'workflow_mode': start_data.mode,
                        'workflow_description': start_data.description,
                        'chained_input': True,
                        'input_length': len(chain_input)
                    }
                )
            else:
                # Standard start node (no chain input)
                logger.info(
                    f"Workflow '{start_data.title}' started in {start_data.mode} mode"
                )

                return NodeExecutionResult(
                    success=True,
                    output=f"Workflow '{start_data.title}' initialized",
                    metadata={
                        'workflow_title': start_data.title,
                        'workflow_mode': start_data.mode,
                        'workflow_description': start_data.description,
                        'chained_input': False
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

    async def _extract_chain_input(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> Optional[str]:
        """
        Extract input from previous workflow chain for chained start nodes.

        Looks for Chat Output nodes connected to this start node and returns
        their output as input for the new chain.

        Args:
            node: The start node to get input for
            context: Execution context with previous node results

        Returns:
            Output text from previous chain, or None if no chain input
        """
        if not context.previous_results:
            return None

        # Look through previous results for connected nodes
        # Previous results are already filtered by dependency in WorkflowExecutionService
        for node_id, result in context.previous_results.items():
            # Check if this is a Chat Output node with actual output
            node_type = result.get('node_type', '')
            output = result.get('output', '')

            if node_type == 'chatOutput' and output:
                logger.debug(
                    f"Found chain input from Chat Output node {node_id}: "
                    f"{len(output)} chars"
                )
                return output

            # Also accept output from other node types if they have content
            if output and output != f"Workflow '{result.get('workflow_title', '')}' initialized":
                logger.debug(
                    f"Found chain input from {node_type} node {node_id}: "
                    f"{len(output)} chars"
                )
                return output

        return None
