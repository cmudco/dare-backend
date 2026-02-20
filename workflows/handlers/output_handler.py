"""
Output node handler for workflow execution.

This handler stores and formats final output from workflow steps.
"""
import logging
from typing import Optional
from channels.db import database_sync_to_async
from django.utils import timezone

from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)
from workflows.handlers.utils.constants import NodeType
from workflows.models import ChatOutputNodeData
from conversations.services.websocket_response_service import WebSocketResponseService


logger = logging.getLogger(__name__)


class OutputNodeHandler(BaseNodeHandler):
    """
    Handler for 'chatOutput' type nodes.

    This handler stores and formats the final output from its corresponding
    step node, making it available for the workflow execution results.
    """

    def can_handle(self, node_type: str) -> bool:
        """Check if this handler can process 'chatOutput' nodes."""
        return node_type == NodeType.CHAT_OUTPUT

    async def execute(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> NodeExecutionResult:
        """
        Execute an output node by retrieving result from its source step via edges.

        This handler uses edge-based data flow to find the correct source node:
        1. Find the edge pointing to this output node
        2. Get the source node from that edge
        3. Look up that node's result in previous_results
        4. Use that specific result as the output content

        Args:
            node: The output node to execute
            context: Execution context with previous results (edge-filtered)

        Returns:
            NodeExecutionResult with the formatted output from source step
        """
        try:
            # Send step_started event for output node
            if context.send_callback:
                try:
                    started_at = timezone.now()
                    await context.send_callback(
                        WebSocketResponseService.format_workflow_step_started(
                            node_id=node.id,
                            step_number=node.step_number or 0,
                            node_type="chatOutput",
                            started_at=started_at,
                            workflow_run_id=context.workflow_run.id
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to send output step_started event: {e}")

            # Get output data from database
            output_data = await database_sync_to_async(
                lambda: node.db_node.data_object
            )()

            if not output_data or not isinstance(output_data, ChatOutputNodeData):
                return NodeExecutionResult(
                    success=False,
                    error="Invalid output node data"
                )

            # Find source step node via edges
            source_output = await self._get_source_step_output(node, context)

            if source_output is None:
                # No source found
                await database_sync_to_async(
                    lambda: ChatOutputNodeData.objects.filter(id=output_data.id).update(
                        status="failed",
                        response="",
                        error="No input received from source step node"
                    )
                )()

                # Send step_completed event with failed status
                if context.send_callback:
                    try:
                        await context.send_callback(
                            WebSocketResponseService.format_workflow_step_completed(
                                node_id=node.id,
                                response="",
                                status="failed",
                                workflow_run_id=context.workflow_run.id
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send output step_completed event: {e}")

                return NodeExecutionResult(
                    success=False,
                    error="No input received from source step node",
                    metadata={'output_node_updated': True, 'status': 'failed'}
                )

            # Successfully found source output
            await database_sync_to_async(
                lambda: ChatOutputNodeData.objects.filter(id=output_data.id).update(
                    status="completed",
                    response=source_output,
                    error=""
                )
            )()

            logger.info(f"Successfully updated output node {node.id}")

            # Send step_completed event for output node
            if context.send_callback:
                try:
                    await context.send_callback(
                        WebSocketResponseService.format_workflow_step_completed(
                            node_id=node.id,
                            response=source_output,
                            status="completed",
                            workflow_run_id=context.workflow_run.id
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to send output step_completed event: {e}")

            return NodeExecutionResult(
                success=True,
                output=source_output,
                metadata={
                    'output_node_updated': True,
                    'status': 'completed'
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

    async def _get_source_step_output(
        self,
        node: ExecutionNode,
        context: NodeExecutionContext
    ) -> Optional[str]:
        """
        Get output from the source step node via edge traversal.

        The previous_results dict is already filtered to only direct dependencies
        by _get_node_dependency_results(), so we just need to find the step node.

        Args:
            node: The current output node
            context: Execution context with previous results

        Returns:
            Output string from source step, or None if not found
        """
        # The previous_results dict is already filtered to only direct dependencies
        # by _get_node_dependency_results(), so we just need to find the step node

        for node_id, result_data in context.previous_results.items():
            # Check if this is a valid result from a step node
            if not result_data:
                continue

            # Skip skipped nodes
            metadata = result_data.get('metadata', {})
            if metadata and metadata.get('skipped'):
                continue

            # Get the output
            output = result_data.get('output')
            if output:
                logger.debug(
                    f"Output node {node.id} found source output from node {node_id}"
                )
                return output

        # No valid source found
        logger.warning(
            f"Output node {node.id} could not find source step output in previous_results"
        )
        return None
