"""
DARE Tool Handler Service.

Orchestrates DARE tool execution within chat conversations.
Handles tool call execution, WebSocket notifications, database persistence,
and follow-up LLM calls for multi-turn tool use.

Similar to mcp_tool_handler.py but for internal DARE tools.
"""

import json
import logging
import time
from typing import List, Dict, Any, Callable, Optional

from channels.db import database_sync_to_async
from djangorestframework_camel_case.util import camelize
from django.utils import timezone

from conversations.constants import SenderType, DEFAULT_AI_SENDER_NAME
from conversations.models import MessageToolCall
from conversations.services.websocket_response_service import WebSocketResponseService
from core.services.dtos.builder import LLMQueryRequestBuilder
from core.services.llm_service import LLMService
from dare_tools.constants import ExecutionStatus
from dare_tools.models import DareTool, DareToolExecution
from dare_tools.services.registry import DareToolRegistry

logger = logging.getLogger(__name__)


class DareToolHandler:
    """
    Orchestrates DARE tool execution within chat conversations.
    
    Handles the complete tool execution lifecycle:
    1. Parse and execute tool calls from LLM response
    2. Emit WebSocket notifications for UI updates
    3. Persist execution records to database
    4. Make follow-up LLM calls with tool results
    """
    
    def __init__(self):
        self.llm_service = LLMService()
    
    async def handle_tool_calls(
        self,
        tool_calls: List[Dict],
        message: 'Message',
        user,
        conversation: 'Conversation',
        send_callback: Callable,
    ) -> List[Dict]:
        """
        Handle DARE tool calls from LLM response.
        
        Filters tool_calls to only DARE tools, executes them, and returns results.
        
        Args:
            tool_calls: List of tool calls from LLM response
            message: The Message model instance
            user: User who triggered the message
            conversation: Conversation context
            send_callback: Async function to send WebSocket messages
            
        Returns:
            List of tool results for follow-up LLM call
        """
        dare_tool_results = []
        bot_message_id = message.id
        
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "")
            tool_call_id = tool_call.get("id", "")
            arguments_raw = tool_call.get("arguments", "{}")
            
            # Skip if not a DARE tool
            if not DareToolRegistry.is_dare_tool(tool_name):
                continue
            
            # Parse arguments
            if isinstance(arguments_raw, str):
                try:
                    arguments = json.loads(arguments_raw)
                except json.JSONDecodeError:
                    arguments = {}
            else:
                arguments = arguments_raw
            
            logger.info(f"[DareToolHandler] Executing DARE tool: {tool_name}")
            
            # Send "running" status to frontend
            await self._send_tool_status(
                send_callback=send_callback,
                bot_message_id=bot_message_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status="running",
                arguments=arguments,
            )
            
            # Execute the tool
            start_time = time.time()
            try:
                result = DareToolRegistry.execute_tool(tool_name, arguments)
                execution_time_ms = int((time.time() - start_time) * 1000)
                
                if result.get("success"):
                    status = ExecutionStatus.COMPLETED
                    error_message = ""
                else:
                    status = ExecutionStatus.FAILED
                    error_message = result.get("error", "Unknown error")
                
            except Exception as e:
                execution_time_ms = int((time.time() - start_time) * 1000)
                result = {"success": False, "error": str(e)}
                status = ExecutionStatus.FAILED
                error_message = str(e)
                logger.exception(f"[DareToolHandler] Error executing {tool_name}: {e}")
            
            # Save execution record
            await self._save_tool_execution(
                user=user,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                message=message,
                conversation=conversation,
                arguments=arguments,
                status=status,
                result=result,
                error_message=error_message,
                execution_time_ms=execution_time_ms,
            )
            
            # Send result status to frontend
            await self._send_tool_status(
                send_callback=send_callback,
                bot_message_id=bot_message_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status="completed" if status == ExecutionStatus.COMPLETED else "failed",
                arguments=arguments,
                result=result,
            )
            
            # Also save to MessageToolCall so it appears in conversation history
            await self._save_message_tool_call(
                message=message,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                status="completed" if status == ExecutionStatus.COMPLETED else "failed",
                result=result,
                error_message=error_message,
            )
            
            # Build result for LLM follow-up
            result_text = self._format_result_for_llm(tool_name, result)
            dare_tool_results.append({
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result": result_text,
            })
        
        return dare_tool_results
    
    async def stream_tool_result_response(
        self,
        tool_results: List[Dict],
        message_data: Dict[str, Any],
        message_obj: 'Message',
        llm: 'LLM',
        conversation: 'Conversation',
        user,
        platform: str,
        send_callback: Callable,
        regenerate: bool = False,
    ) -> str:
        """
        Make a follow-up LLM call with tool results to get the final response.
        
        Args:
            tool_results: List of tool execution results
            message_data: Original message data
            message_obj: Message object to update
            llm: LLM instance
            conversation: Conversation context
            user: User who triggered the request
            platform: Platform name
            send_callback: WebSocket callback
            regenerate: Whether this is a regeneration
            
        Returns:
            Final AI response text
        """
        # Build request with tool results
        request = LLMQueryRequestBuilder.from_message_data(
            message=message_data["message"],
            conversation=conversation,
            user=user,
            message_data=message_data,
            llm=llm,
            message_obj=message_obj,
            platform=platform,
        )
        
        # Add tool results to the request
        request.tool_results = tool_results
        
        # Stream the response
        ai_response_accumulator = ""
        
        async for chunk, usage in self.llm_service.query(request):
            if chunk and chunk.strip():
                ai_response_accumulator += chunk
                payload = WebSocketResponseService.format_streaming_chunk(
                    message_id=message_obj.id,
                    chunk=ai_response_accumulator,
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
                await send_callback(camelize(payload))
        
        return ai_response_accumulator
    
    def _format_result_for_llm(self, tool_name: str, result: Dict) -> str:
        """Format tool result as text for LLM context."""
        if not result.get("success"):
            return f"Error: {result.get('error', 'Unknown error')}"
        
        if tool_name == "create_diagram":
            return f"Diagram created successfully. Mermaid code:\n```mermaid\n{result.get('mermaid_code', '')}\n```"
        elif tool_name == "create_chart":
            chart_config = result.get("chart_config", {})
            return f"Chart created successfully. Type: {chart_config.get('type')}, Title: {chart_config.get('title')}"
        else:
            return json.dumps(result)
    
    async def _send_tool_status(
        self,
        send_callback: Callable,
        bot_message_id: int,
        tool_call_id: str,
        tool_name: str,
        status: str,
        arguments: Dict = None,
        result: Dict = None,
    ):
        """Send tool call status update to frontend via WebSocket."""
        # Use camelCase for type value since camelize() only converts keys, not string values
        event_type = "dareToolCall" if status == "running" else "dareToolResult"
        payload = {
            "type": event_type,
            "message_id": bot_message_id,
            "tool_call": {
                "id": tool_call_id,
                "tool_name": tool_name,
                "tool_slug": tool_name,  # For DARE tools, name == slug
                "server_slug": "dare",  # Special slug for DARE tools
                "status": status,
                "arguments": arguments or {},
            }
        }
        
        if result is not None:
            payload["tool_call"]["result"] = result
        
        camelized = camelize(payload)
        logger.info(f"[DareToolHandler] Sending tool status: type={camelized.get('type')}, status={status}, message_id={bot_message_id}")
        await send_callback(camelized)
    
    @database_sync_to_async
    def _save_tool_execution(
        self,
        user,
        tool_name: str,
        tool_call_id: str,
        message: 'Message',
        conversation: 'Conversation',
        arguments: Dict,
        status: str,
        result: Dict,
        error_message: str,
        execution_time_ms: int,
    ):
        """Save tool execution record to database."""
        try:
            # Get or create the tool record
            tool = DareTool.active_objects.filter(function_name=tool_name).first()
            
            if not tool:
                logger.warning(f"DareTool not found for function_name: {tool_name}")
                return
            
            DareToolExecution.all_objects.create(
                user=user,
                tool=tool,
                message=message,
                conversation=conversation,
                tool_call_id=tool_call_id,
                arguments=arguments,
                status=status,
                result=result,
                error_message=error_message,
                execution_time_ms=execution_time_ms,
            )
        except Exception as e:
            logger.exception(f"Failed to save DareToolExecution: {e}")
    
    @database_sync_to_async
    def _save_message_tool_call(
        self,
        message: 'Message',
        tool_call_id: str,
        tool_name: str,
        arguments: Dict,
        status: str,
        result: Dict = None,
        error_message: str = "",
    ):
        """
        Save MessageToolCall record so it appears in conversation history.
        
        Uses server_slug='dare' to distinguish from MCP tool calls.
        """

        
        try:
            # Format result as string for storage
            result_text = None
            if result and result.get("success"):
                result_text = json.dumps(result, indent=2)[:5000]
            
            MessageToolCall.objects.create(
                message=message,
                tool_call_id=tool_call_id,
                server_slug="dare",  # Distinguish from MCP tools
                tool_name=tool_name,
                arguments=arguments,
                status=status,
                result=result_text,
                error=error_message if error_message else None,
                executed_at=timezone.now(),
            )
            logger.debug(f"[DareToolHandler] Saved MessageToolCall: dare.{tool_name} ({status})")
        except Exception as e:
            logger.error(f"[DareToolHandler] Failed to save MessageToolCall: {e}")


# Global handler instance
dare_tool_handler = DareToolHandler()
