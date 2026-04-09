"""
Output node handler for workflow execution.

ChatOutput nodes are display-only — they show what their connected step
produced. As of Phase 2, chatOutput is in NON_EXECUTABLE_TYPES and this
handler will not be called during normal execution. It exists only as a
safety net: if somehow invoked, it returns the source step's output
without any DB writes or WebSocket events.

Phase 5 (frontend) will add a selector that derives output node state
from the source step, and this file can be deleted entirely.
"""
import logging
from typing import Optional

from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
)
from workflows.handlers.utils.constants import NodeType


logger = logging.getLogger(__name__)


class OutputNodeHandler(BaseNodeHandler):
    """
    Handler for 'chatOutput' type nodes.

    This is a no-op handler. ChatOutput nodes are non-executable as of Phase 2.
    If invoked (e.g. by a test or manual call), it returns the source step's
    output from previous_results without any DB writes.
    """

    def can_handle(self, node_type: str) -> bool:
        return node_type == NodeType.CHAT_OUTPUT

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext,
    ) -> NodeExecutionResult:
        """
        Pass-through: return the first valid output from previous_results.

        No DB writes, no WebSocket events, no ChatOutputNodeData mutation.
        """
        source_output = self._get_source_output(context)

        if source_output is None:
            return NodeExecutionResult(
                success=False,
                error="No input received from source step node",
            )

        return NodeExecutionResult(
            success=True,
            output=source_output,
        )

    @staticmethod
    def _get_source_output(context: NodeExecutionContext) -> Optional[str]:
        """Get the first valid output from previous_results."""
        for node_id, result_data in context.previous_results.items():
            if not result_data:
                continue

            metadata = result_data.get('metadata', {})
            if metadata and metadata.get('skipped'):
                continue

            output = result_data.get('output')
            if output:
                return output

        return None
