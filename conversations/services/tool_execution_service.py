"""
Tool Execution Service.

Executes one round of model tool calls, whatever their origin:

- DARE native tools — retrieval (``search_documents``), artifact tools
  (charts/diagrams/docs), or plain registry executors.
- MCP tools — ``server__tool``-named calls dispatched through the user's
  connected MCP servers.

For every call it emits the unified lifecycle events (executing → result),
persists a ``MessageToolCall`` row (with the loop round) plus the
``DareToolExecution`` audit row for DARE tools, and returns typed
``ToolCallResult`` objects whose ``content`` is the text the model reads
in its ``role:"tool"`` turn. Failures never raise — they come back as
error results so the model can see and react to them.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.utils import timezone

from conversations.constants import ToolCallOrigin
from conversations.models import Conversation, Message, MessageToolCall
from conversations.services.artifact_tool_executor import \
    artifact_tool_executor
from conversations.services.tool_event_service import ToolEventEmitter
from core.services.dtos import ToolCallRequest, ToolCallResult
from dare_tools.constants import ExecutionStatus
from dare_tools.models import DareTool, DareToolExecution
from dare_tools.services.registry import DareToolRegistry
from dare_tools.services.result_formatters import format_dare_result_for_llm
from dare_tools.services.retrieval_tool_executor import (
    RetrievalScope, retrieval_tool_executor)
from mcp.services.mcp_tool_executor import (MCPToolExecutorError,
                                            mcp_tool_executor)

logger = logging.getLogger(__name__)

DARE_SERVER_SLUG = "dare"

# DARE tools that create visual artifacts — routed to ArtifactToolExecutor.
ARTIFACT_TOOLS = frozenset(
    {
        "create_chart",
        "create_diagram",
        "create_docx",
        "create_pptx",
        "update_artifact",
        "update_artifact_inline",
        "create_react_component",
    }
)

# DARE tools that retrieve document context — routed to RetrievalToolExecutor.
RETRIEVAL_TOOLS = frozenset({"search_documents"})

MAX_PERSISTED_RESULT_CHARS = 5000


@dataclass(frozen=True)
class ToolExecutionContext:
    """Everything a round of tool execution needs from the chat turn."""

    message: Message
    conversation: Conversation
    user: Any
    send_callback: Callable
    emitter: ToolEventEmitter
    retrieval_scope: Optional[RetrievalScope] = None


class ToolExecutionService:
    """Executes model tool calls and persists their outcomes."""

    @staticmethod
    def _serialize_persisted_result(raw_result: Dict) -> str:
        """Serialize a bounded, valid JSON result for conversation history."""
        serialized = json.dumps(raw_result)
        if len(serialized) <= MAX_PERSISTED_RESULT_CHARS:
            return serialized

        compact_result = {
            "truncated": True,
            "original_chars": len(serialized),
            "content_preview": serialized[: MAX_PERSISTED_RESULT_CHARS - 500],
        }
        for key in ("success", "artifactId", "artifact_id", "message", "error"):
            if key in raw_result:
                compact_result[key] = raw_result[key]

        # The preserved metadata can itself be unexpectedly large. Reduce the
        # preview until the envelope remains within the database/UI limit.
        compact_serialized = json.dumps(compact_result)
        overflow = len(compact_serialized) - MAX_PERSISTED_RESULT_CHARS
        if overflow > 0:
            compact_result["content_preview"] = compact_result[
                "content_preview"
            ][: -(overflow + 1)]
            compact_serialized = json.dumps(compact_result)
        return compact_serialized

    async def execute_round(
        self,
        tool_calls: List[ToolCallRequest],
        ctx: ToolExecutionContext,
        round_index: int,
    ) -> List[ToolCallResult]:
        """Execute every call of one loop round, in model order.

        Args:
            tool_calls: Completed calls collected from the round's stream.
            ctx: Execution context for the turn.
            round_index: 1-based loop round.

        Returns:
            One ToolCallResult per call — errors included, never raises.
        """
        results: List[ToolCallResult] = []
        for call in tool_calls:
            results.append(await self._execute_one(call, ctx, round_index))
        return results

    async def _execute_one(
        self,
        call: ToolCallRequest,
        ctx: ToolExecutionContext,
        round_index: int,
    ) -> ToolCallResult:
        origin, server_slug, bare_tool_name = self._classify(call.name)
        arguments = self._parse_arguments(call.arguments)

        await ctx.emitter.tool_call_executing(
            call.id, call.name, server_slug, origin, round_index, arguments
        )

        start_time = time.time()
        try:
            if origin == ToolCallOrigin.DARE:
                raw_result, content, is_error = await self._execute_dare(
                    call.name, arguments, ctx
                )
            elif origin == ToolCallOrigin.MCP:
                raw_result, content, is_error = await self._execute_mcp(
                    server_slug, bare_tool_name, arguments, ctx
                )
            else:
                raw_result = {"success": False, "error": f"Unknown tool: {call.name}"}
                content = f"Error: unknown tool '{call.name}'"
                is_error = True
        except MCPToolExecutorError as exc:
            raw_result = {"success": False, "error": str(exc)}
            content = f"Error: {exc}"
            is_error = True
        except Exception as exc:
            logger.exception(
                "[ToolExecutionService] %s failed in round %s", call.name, round_index
            )
            raw_result = {"success": False, "error": str(exc)}
            content = f"Error: {exc}"
            is_error = True
        execution_time_ms = int((time.time() - start_time) * 1000)

        error_message = raw_result.get("error", "") if is_error else ""

        if origin == ToolCallOrigin.DARE:
            await self._save_dare_execution(
                ctx,
                call,
                arguments,
                raw_result,
                is_error,
                error_message,
                execution_time_ms,
            )

        await self._save_message_tool_call(
            ctx.message,
            call,
            server_slug,
            origin,
            arguments,
            raw_result,
            is_error,
            error_message,
            round_index,
        )

        await ctx.emitter.tool_call_result(
            call.id,
            call.name,
            server_slug,
            origin,
            round_index,
            status="failed" if is_error else "completed",
            result=raw_result,
            error=error_message or None,
        )

        return ToolCallResult(
            tool_call_id=call.id,
            tool_name=call.name,
            origin=origin,
            server_slug=server_slug,
            content=content,
            is_error=is_error,
            raw_result=raw_result,
        )

    # ========== Routing ==========

    @staticmethod
    def _classify(tool_name: str) -> Tuple[str, str, str]:
        """Resolve (origin, server_slug, bare_tool_name) for a call name."""
        if DareToolRegistry.is_dare_tool(tool_name):
            return ToolCallOrigin.DARE, DARE_SERVER_SLUG, tool_name
        if "__" in tool_name:
            server_slug, bare = tool_name.split("__", 1)
            return ToolCallOrigin.MCP, server_slug, bare
        return "unknown", "unknown", tool_name

    async def _execute_dare(
        self, tool_name: str, arguments: Dict, ctx: ToolExecutionContext
    ) -> Tuple[Dict, str, bool]:
        if tool_name in RETRIEVAL_TOOLS:
            raw_result = await retrieval_tool_executor.execute(
                arguments=arguments,
                message=ctx.message,
                scope=ctx.retrieval_scope,
            )
        elif tool_name in ARTIFACT_TOOLS:
            raw_result = await artifact_tool_executor.execute(
                tool_name=tool_name,
                arguments=arguments,
                message=ctx.message,
                conversation=ctx.conversation,
                send_callback=ctx.send_callback,
            )
        else:
            raw_result = await sync_to_async(DareToolRegistry.execute_tool)(
                tool_name, arguments
            )

        is_error = not raw_result.get("success", False)
        content = format_dare_result_for_llm(tool_name, raw_result)
        return raw_result, content, is_error

    async def _execute_mcp(
        self,
        server_slug: str,
        bare_tool_name: str,
        arguments: Dict,
        ctx: ToolExecutionContext,
    ) -> Tuple[Dict, str, bool]:
        raw_result = await mcp_tool_executor.execute_tool_call(
            user=ctx.user,
            server_slug=server_slug,
            tool_name=bare_tool_name,
            arguments=arguments,
            message=ctx.message,
            conversation=ctx.conversation,
        )
        content = self._extract_mcp_result_text(raw_result)
        raw_dict = (
            raw_result if isinstance(raw_result, dict) else {"result": raw_result}
        )
        return raw_dict, content, False

    @staticmethod
    def _extract_mcp_result_text(result: Any) -> str:
        """Extract the model-facing text from an MCP tool result."""
        if isinstance(result, dict):
            content = result.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", str(result))
            return str(result)
        return str(result)

    @staticmethod
    def _parse_arguments(arguments: str) -> Dict:
        try:
            parsed = json.loads(arguments or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    # ========== Persistence ==========

    @database_sync_to_async
    def _save_dare_execution(
        self,
        ctx: ToolExecutionContext,
        call: ToolCallRequest,
        arguments: Dict,
        raw_result: Dict,
        is_error: bool,
        error_message: str,
        execution_time_ms: int,
    ) -> None:
        """Persist the DareToolExecution audit row (best-effort)."""
        try:
            tool = DareTool.active_objects.filter(function_name=call.name).first()
            if not tool:
                logger.warning("DareTool not found for function_name: %s", call.name)
                return
            DareToolExecution.all_objects.create(
                user=ctx.user,
                tool=tool,
                message=ctx.message,
                conversation=ctx.conversation,
                tool_call_id=call.id,
                arguments=arguments,
                status=(
                    ExecutionStatus.FAILED if is_error else ExecutionStatus.COMPLETED
                ),
                result=raw_result,
                error_message=error_message,
                execution_time_ms=execution_time_ms,
            )
        except Exception as exc:
            logger.exception("Failed to save DareToolExecution: %s", exc)

    @database_sync_to_async
    def _save_message_tool_call(
        self,
        message: Message,
        call: ToolCallRequest,
        server_slug: str,
        origin: str,
        arguments: Dict,
        raw_result: Dict,
        is_error: bool,
        error_message: str,
        round_index: int,
    ) -> None:
        """Persist the MessageToolCall row rendered in conversation history."""
        try:
            result_text = None
            if raw_result and not is_error:
                result_text = self._serialize_persisted_result(raw_result)
            MessageToolCall.objects.create(
                message=message,
                tool_call_id=call.id,
                server_slug=server_slug,
                origin=(
                    origin if origin in ToolCallOrigin.values else ToolCallOrigin.MCP
                ),
                tool_name=call.name,
                arguments=arguments,
                status="failed" if is_error else "completed",
                result=result_text,
                error=error_message or None,
                executed_at=timezone.now(),
                round_index=round_index,
            )
        except Exception as exc:
            logger.error("Failed to save MessageToolCall: %s", exc)


# Global service instance
tool_execution_service = ToolExecutionService()
