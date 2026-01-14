"""
MCP Tool Handler Service.

Orchestrates MCP tool execution within chat conversations.
Handles tool call execution, WebSocket notifications, database persistence,
and follow-up LLM calls for multi-turn tool use.

This separates tool execution orchestration from the MessageCoordinator,
which should only coordinate message flow.
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from conversations.constants import SenderType, DEFAULT_AI_SENDER_NAME
from conversations.models import Conversation, Message, MessageToolCall
from core.services.dtos import LLMQueryRequest
from core.services.dtos.builder import LLMQueryRequestBuilder
from core.services.llm_service import LLMService
from mcp.services.mcp_tool_executor import mcp_tool_executor, MCPToolExecutorError

logger = logging.getLogger(__name__)


class MCPToolHandler:
    """
    Orchestrates MCP tool execution within chat conversations.
    
    Handles the complete tool execution lifecycle:
    1. Parse and execute tool calls from LLM response
    2. Emit WebSocket notifications for UI updates
    3. Save MessageToolCall records for audit trail
    4. Make follow-up LLM calls with tool results
    
    Usage:
        handler = MCPToolHandler()
        results = await handler.handle_tool_calls(
            tool_calls=usage["tool_calls"],
            message=message_obj,
            user=user,
            conversation=conversation,
            send_callback=websocket_send,
        )
    """
    
    def __init__(self):
        self.llm_service = LLMService()
    
    async def handle_tool_calls(
        self,
        tool_calls: List[Dict],
        message: Message,
        user,
        conversation: Conversation,
        send_callback: Callable,
    ) -> List[Dict]:
        """
        Handle MCP tool calls from LLM response.
        
        Executes each tool call and streams results back to the client.
        Returns the results for follow-up LLM call.
        
        Args:
            tool_calls: List of tool call dicts with name, arguments, and id
            message: AI message object for context
            user: User instance
            conversation: Conversation instance
            send_callback: Async callback for WebSocket notifications
            
        Returns:
            List of tool result dicts with tool_call_id, tool_name, and result
        """
        results = []
        bot_message_id = message.id
        
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "")
            tool_call_id = tool_call.get("id", "")
            arguments_str = tool_call.get("arguments", "{}")
            
            # Skip non-MCP tool calls (e.g., web search)
            if "__" not in tool_name:
                continue

            # Initialize variables before try block to ensure they're always defined
            server_slug = "unknown"
            actual_tool_name = tool_name
            arguments = {}

            try:
                # Parse tool name to get server and actual tool name
                server_slug, actual_tool_name = mcp_tool_executor.parse_tool_call_name(
                    tool_name
                )
                
                # Parse arguments
                try:
                    arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                except json.JSONDecodeError:
                    arguments = {}

                logger.debug(
                    f"[MCPToolHandler] Executing MCP tool: {actual_tool_name} "
                    f"on {server_slug}"
                )

                # Send tool execution notification to client
                tool_start_payload = {
                    "type": "mcp_tool_call",
                    "messageId": bot_message_id,
                    "toolName": actual_tool_name,
                    "serverSlug": server_slug,
                    "status": "executing",
                }
                await send_callback(tool_start_payload)

                # Execute the tool
                result = await mcp_tool_executor.execute_tool_call(
                    user=user,
                    server_slug=server_slug,
                    tool_name=actual_tool_name,
                    arguments=arguments,
                    message=message,
                    conversation=conversation,
                )
                
                # Extract result text
                result_text = self._extract_result_text(result)

                # Send tool result to client
                tool_result_payload = {
                    "type": "mcp_tool_result",
                    "messageId": bot_message_id,
                    "toolName": actual_tool_name,
                    "serverSlug": server_slug,
                    "status": "success",
                    "result": result,
                }
                await send_callback(tool_result_payload)

                logger.debug(
                    f"[MCPToolHandler] MCP tool {actual_tool_name} executed successfully"
                )
                
                # Save MessageToolCall to database for UI display
                await self._save_message_tool_call(
                    message=message,
                    tool_call_id=tool_call_id,
                    server_slug=server_slug,
                    tool_name=actual_tool_name,
                    arguments=arguments,
                    status='completed',
                    result=result_text[:5000] if result_text else None,
                )
                
                # Add to results for follow-up LLM call
                results.append({
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "result": result_text,
                })

            except MCPToolExecutorError as e:
                # Use defensive try/except to ensure we ALWAYS append a result
                try:
                    error_result = await self._handle_tool_error(
                        error=e,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        server_slug=server_slug,
                        actual_tool_name=actual_tool_name,
                        arguments=arguments,
                        message=message,
                        bot_message_id=bot_message_id,
                        send_callback=send_callback,
                        is_expected=True,
                    )
                    results.append(error_result)
                except Exception as handler_error:
                    # If even the error handler fails, create a minimal result
                    logger.exception(f"[MCPToolHandler] Error handler itself failed: {handler_error}")
                    results.append({
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "result": f"Error: {str(e)}",
                    })

            except Exception as e:
                # Use defensive try/except to ensure we ALWAYS append a result
                try:
                    error_result = await self._handle_tool_error(
                        error=e,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        server_slug=server_slug,
                        actual_tool_name=actual_tool_name,
                        arguments=arguments,
                        message=message,
                        bot_message_id=bot_message_id,
                        send_callback=send_callback,
                        is_expected=False,
                    )
                    results.append(error_result)
                except Exception as handler_error:
                    # If even the error handler fails, create a minimal result
                    logger.exception(f"[MCPToolHandler] Error handler itself failed: {handler_error}")
                    results.append({
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "result": f"Error: {str(e)}",
                    })
        
        return results
    
    async def stream_tool_result_response(
        self,
        tool_results: List[Dict],
        message_data: Dict[str, Any],
        message_obj: Message,
        llm,
        conversation: Conversation,
        user,
        platform: str,
        send_callback: Callable,
        regenerate: bool = False,
    ) -> str:
        """
        Make a follow-up LLM call with tool results to get the final response.
        
        This implements the second part of multi-turn tool use:
        1. LLM calls tool -> we execute it
        2. Feed results back -> LLM generates human-readable response
        
        Args:
            tool_results: List of tool results from handle_tool_calls
            message_data: Original message data
            message_obj: AI message object
            llm: LLM model to use
            conversation: Conversation instance
            user: User instance
            platform: Platform name (DARE/SocraticBots)
            send_callback: Async callback for WebSocket streaming
            regenerate: Whether this is a regeneration
            
        Returns:
            The accumulated response text
        """
        from conversations.services.websocket_response_service import WebSocketResponseService
        
        bot_message_id = message_obj.id
        
        # Build tool results context
        tool_context = "Here are the results from the tools I just used:\n\n"
        for tr in tool_results:
            tool_context += f"**{tr['tool_name']}** result:\n```\n{tr['result'][:2000]}\n```\n\n"
        tool_context += "Please summarize these results in a helpful way for the user."
        
        # Build a new request with tool results as the message
        request = LLMQueryRequestBuilder.from_message_data(
            message=tool_context,
            conversation=conversation,
            user=user,
            message_data=message_data,
            llm=llm,
            message_obj=message_obj,
            platform=platform,
        )
        
        response_accumulator = ""
        
        # Stream the follow-up response
        async for chunk, usage in self.llm_service.query(request):
            if chunk and chunk.strip():
                response_accumulator += chunk
                payload = WebSocketResponseService.format_streaming_chunk(
                    message_id=bot_message_id,
                    chunk=response_accumulator,
                    is_complete=False,
                    metadata={
                        "senderName": DEFAULT_AI_SENDER_NAME,
                        "senderType": SenderType.AI_ASSISTANT,
                        "isSender": False,
                        "streaming": True,
                        "regenerate": regenerate,
                        "date": message_obj.created_at.isoformat(),
                    }
                )
                await send_callback(payload)
        
        return response_accumulator
    
    # ========== Private Helper Methods ==========
    
    def _extract_result_text(self, result: Any) -> str:
        """Extract text from tool result."""
        if isinstance(result, dict):
            content = result.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", str(result))
            else:
                return str(result)
        else:
            return str(result)
    
    async def _handle_tool_error(
        self,
        error: Exception,
        tool_call_id: str,
        tool_name: str,
        server_slug: str,
        actual_tool_name: str,
        arguments: dict,
        message: Message,
        bot_message_id: int,
        send_callback: Callable,
        is_expected: bool,
    ) -> Dict:
        """Handle tool execution error and return error result for LLM."""
        error_prefix = "" if is_expected else "Unexpected "
        error_msg = f"{error_prefix}error: {str(error)}"
        log_method = logger.error if is_expected else logger.exception
        log_method(f"[MCPToolHandler] MCP tool execution failed: {error}")
        
        # Send error to client - MUST match mcp_tool_call format (toolName, serverSlug)
        tool_error_payload = {
            "type": "mcp_tool_result",
            "messageId": bot_message_id,
            "toolName": actual_tool_name,  # Use actual tool name, not prefixed
            "serverSlug": server_slug,  # Include serverSlug for frontend matching
            "status": "error",
            "error": str(error),
        }
        await send_callback(tool_error_payload)
        
        # Save failed tool call to database
        await self._save_message_tool_call(
            message=message,
            tool_call_id=tool_call_id,
            server_slug=server_slug,
            tool_name=actual_tool_name,
            arguments=arguments,
            status='failed',
            error=str(error),
        )
        
        # Return error result for LLM
        return {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": f"Error: {str(error)}",
        }
    
    async def _save_message_tool_call(
        self,
        message: Message,
        tool_call_id: str,
        server_slug: str,
        tool_name: str,
        arguments: dict,
        status: str,
        result: str = None,
        error: str = None,
    ):
        """
        Save a MessageToolCall record to database.
        
        Args:
            message: The Message model instance this tool call belongs to
            tool_call_id: Unique ID from the LLM
            server_slug: MCP server slug (e.g., 'slack')
            tool_name: Name of the tool executed
            arguments: Arguments passed to the tool
            status: Current status ('completed' or 'failed')
            result: Result text if successful
            error: Error message if failed
        """
        @sync_to_async
        def create_tool_call():
            return MessageToolCall.objects.create(
                message=message,
                tool_call_id=tool_call_id,
                server_slug=server_slug,
                tool_name=tool_name,
                arguments=arguments,
                status=status,
                result=result,
                error=error,
                executed_at=timezone.now(),
            )
        
        try:
            await create_tool_call()
            logger.debug(f"[MCPToolHandler] Saved MessageToolCall: {server_slug}.{tool_name} ({status})")
        except Exception as e:
            logger.error(f"[MCPToolHandler] Failed to save MessageToolCall: {e}")


# Global handler instance
mcp_tool_handler = MCPToolHandler()
