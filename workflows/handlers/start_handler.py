"""
Start node handler for workflow execution.

Initializes workflow execution. Supports workflow chaining by extracting
input from previous workflow chains (e.g., Chat Output nodes).
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
from workflows.handlers.utils.constants import NodeType
from workflows.models import StartNodeData


logger = logging.getLogger(__name__)


class StartNodeHandler(BaseNodeHandler):
    """Handler for 'start' type nodes."""

    def can_handle(self, node_type: str) -> bool:
        return node_type == NodeType.START

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """
        Initialize workflow execution.

        If this start node has incoming connections from a previous chain,
        the chain's output is passed downstream.
        """
        try:
            start_data = await database_sync_to_async(lambda: node.db_node.data_object)()

            if not start_data or not isinstance(start_data, StartNodeData):
                return NodeExecutionResult(success=False, error="Invalid start node data")

            chain_input = self._extract_chain_input(context)

            if chain_input:
                logger.info(
                    f"Start node '{start_data.title}' received chain input ({len(chain_input)} chars)"
                )
                return NodeExecutionResult(
                    success=True,
                    output=chain_input,
                    metadata={
                        'workflow_title': start_data.title,
                        'workflow_mode': start_data.mode,
                        'workflow_description': start_data.description,
                        'chained_input': True,
                        'input_length': len(chain_input),
                    },
                )

            logger.info(f"Workflow '{start_data.title}' started in {start_data.mode} mode")
            return NodeExecutionResult(
                success=True,
                output=f"Workflow '{start_data.title}' initialized",
                metadata={
                    'workflow_title': start_data.title,
                    'workflow_mode': start_data.mode,
                    'workflow_description': start_data.description,
                    'chained_input': False,
                },
            )

        except Exception as e:
            error_category, error_type = categorize_error(e)
            logger.error(
                f"{error_category} in start node {node.id} ({error_type}): {e}",
                exc_info=True,
            )
            return NodeExecutionResult(success=False, error=f"{error_category}: {e}")

    @staticmethod
    def _extract_chain_input(context: NodeExecutionContext) -> Optional[str]:
        """
        Extract input from a previous workflow chain.

        Looks through previous_results for connected nodes with output.
        """
        if not context.previous_results:
            return None

        for node_id, result in context.previous_results.items():
            node_type = result.get('node_type', '')
            output = result.get('output', '')

            if node_type == NodeType.CHAT_OUTPUT and output:
                return output

            if output and output != f"Workflow '{result.get('workflow_title', '')}' initialized":
                return output

        return None
