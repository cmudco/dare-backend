"""
Tool Execution Service.

Executes one round of model tool calls, whatever their origin:

- DARE native tools — retrieval (``search_documents``), artifact tools
  (charts/diagrams/docs), or plain registry executors.
- MCP tools — ``server__tool``-named calls dispatched through the user's
  connected MCP servers.

For every call it emits the unified lifecycle events (executing → result),
persists the host's tool-call row through the binding store (with the loop
round) plus the ``DareToolExecution`` audit row for DARE tools, and returns typed
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

from conversations.constants import ToolCallOrigin
from conversations.models import Conversation, Message
from conversations.services.artifact_tool_executor import \
    artifact_tool_executor
from core.services.dtos import ToolCallRequest, ToolCallResult
from core.services.tool_loop.binding import ToolLoopStore
from core.services.tool_loop.events import ToolEventEmitter
from dare_tools.constants import ExecutionStatus
from dare_tools.models import DareTool, DareToolExecution
from dare_tools.services.registry import DareToolRegistry
from dare_tools.services.result_formatters import format_dare_result_for_llm
from dare_tools.services.retrieval_tool_executor import (
    RetrievalScope, retrieval_tool_executor)
from mcp.services.artifact_bridge import (BridgeStatus,
                                          maybe_create_pdf_artifact)
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


@dataclass(frozen=True)
class ToolExecutionContext:
    """Everything a round of tool execution needs from the host turn.

    ``message``/``conversation`` are chat-only: tools that require them
    (artifacts, MCP) return a clean error result when they are None
    (workflow steps), instead of raising.
    """

    message: Optional[Message]
    conversation: Optional[Conversation]
    user: Any
    send_callback: Callable
    emitter: ToolEventEmitter
    store: ToolLoopStore
    retrieval_scope: Optional[RetrievalScope] = None


class ToolExecutionService:
    """Executes model tool calls and persists their outcomes."""

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
        logger.info(
            "[journey] mid=%s round %d tool %s (%s/%s) -> %s in %dms",
            ctx.store.turn_key,
            round_index,
            call.name,
            origin,
            server_slug,
            "failed" if is_error else "ok",
            execution_time_ms,
        )

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

        await ctx.store.save_tool_call(
            call=call,
            server_slug=server_slug,
            origin=origin,
            arguments=arguments,
            raw_result=raw_result,
            is_error=is_error,
            error=error_message,
            round_index=round_index,
            execution_time_ms=execution_time_ms,
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
                target=ctx.store.retrieval_target,
                scope=ctx.retrieval_scope,
            )
        elif tool_name in ARTIFACT_TOOLS:
            if ctx.message is None or ctx.conversation is None:
                return self._unavailable_in_context(tool_name)
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
        if ctx.message is None or ctx.conversation is None:
            return self._unavailable_in_context(f"{server_slug}__{bare_tool_name}")
        raw_result = await mcp_tool_executor.execute_tool_call(
            user=ctx.user,
            server_slug=server_slug,
            tool_name=bare_tool_name,
            arguments=arguments,
            message=ctx.message,
            conversation=ctx.conversation,
        )

        raw_dict = (
            raw_result if isinstance(raw_result, dict) else {"result": raw_result}
        )
        if raw_dict.get("isError", False):
            return raw_dict, self._extract_mcp_result_text(raw_dict), True

        bridge_result = await maybe_create_pdf_artifact(
            raw_result,
            message=ctx.message,
            conversation=ctx.conversation,
            arguments=arguments,
            server_slug=server_slug,
            tool_name=bare_tool_name,
            send_callback=ctx.send_callback,
        )
        if bridge_result.status == BridgeStatus.CREATED:
            bridged = bridge_result.artifact or {}
            raw_result = self._sanitize_bridged_document_result(raw_result, bridged)
            content = (
                f"Document rendered successfully as PDF artifact "
                f"'{bridged['title']}' (version {bridged['version']}). It is "
                "already displayed to the user in the artifact panel with a "
                "download button. Tell them it is ready and briefly summarize "
                "its contents. Do not output any URL or link."
            )
        elif bridge_result.status == BridgeStatus.FAILED:
            content = bridge_result.error
            raw_result = {
                "isError": True,
                "content": [{"type": "text", "text": content}],
            }
        else:
            content = self._extract_mcp_result_text(raw_result)

        raw_dict = (
            raw_result if isinstance(raw_result, dict) else {"result": raw_result}
        )
        is_error = bool(raw_dict.get("isError", False))
        return raw_dict, content, is_error

    @staticmethod
    def _unavailable_in_context(tool_name: str) -> Tuple[Dict, str, bool]:
        """Clean error for tools that need chat context the host lacks."""
        error = f"Tool '{tool_name}' is not available in this execution context"
        return {"success": False, "error": error}, f"Error: {error}", True

    @staticmethod
    def _sanitize_bridged_document_result(result: Any, bridged: Dict) -> Any:
        """Replace an internal PDF URL with the resulting DARE artifact."""
        if not isinstance(result, dict):
            return result

        sanitized = dict(result)
        sanitized["content"] = [
            {
                "type": "text",
                "text": (
                    f"Rendered PDF artifact '{bridged['title']}' "
                    f"(version {bridged['version']})."
                ),
            }
        ]
        if "structuredContent" in sanitized:
            sanitized["structuredContent"] = {
                "artifactId": bridged["artifact_id"],
                "title": bridged["title"],
                "filename": bridged["filename"],
                "mimeType": "application/pdf",
            }
        return sanitized

    @staticmethod
    def _extract_mcp_result_text(result: Any) -> str:
        """Extract the model-facing text from an MCP tool result."""
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        return block["text"]
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
            if ctx.message is None:
                return
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


# Global service instance
tool_execution_service = ToolExecutionService()
