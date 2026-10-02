"""The assistant's implementation of the tool-loop seam.

Tool calls are collected in memory and saved with the finished reply; text
streams to the asking socket as ``assistant_stream`` events. The platform
pays for assistant turns, so the mid-stream billing gate always passes.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from assistant.models import AssistantMessage
from core.services.llm_helpers.retrieval_targets import TransientRetrievalTarget
from core.services.tool_loop.binding import ArtifactHost


class AssistantToolLoopStore:
    def __init__(self, reply: AssistantMessage) -> None:
        self.turn_key = f"as{reply.id}"
        self.retrieval_target = TransientRetrievalTarget()
        self.tool_calls: List[Dict[str, Any]] = []

    async def clear_prior_tool_calls(self) -> None:
        self.tool_calls.clear()

    async def save_context_trace(self, trace: Dict[str, Any]) -> None:
        """Assistant prompts are host-built; there is no assembly trace."""

    async def save_tool_call(
        self,
        *,
        call: Any,
        server_slug: str,
        origin: str,
        arguments: Dict[str, Any],
        raw_result: Dict[str, Any],
        is_error: bool,
        error: str,
        round_index: int,
        execution_time_ms: int,
    ) -> None:
        record = {
            "name": call.name,
            "arguments": arguments,
            "status": "failed" if is_error else "completed",
            "round": round_index,
        }
        if raw_result.get("proposal_id"):
            record["proposal_id"] = raw_result["proposal_id"]
        self.tool_calls.append(record)


class AssistantStreamSink:
    def __init__(self, reply: AssistantMessage, emit: Callable) -> None:
        self._reply_id = reply.id
        self._emit = emit

    async def text(self, accumulated_text: str) -> None:
        await self._emit(
            {
                "type": "assistant_stream",
                "message_id": self._reply_id,
                "content": accumulated_text,
            }
        )

    async def thinking(self, accumulated_text: str, thinking_summary: str) -> None:
        """Reasoning is not shown in the assistant panel."""


class PlatformPaidGate:
    async def check(
        self, usage_totals: Dict[str, Any]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return True, None


class AssistantToolLoopBinding:
    def __init__(self, *, reply: AssistantMessage, user, emit: Callable) -> None:
        self.store = AssistantToolLoopStore(reply)
        self.sink = AssistantStreamSink(reply, emit)
        self.gate = PlatformPaidGate()
        self.correlation = {"message_id": reply.id}
        self.send_callback = emit
        self.user = user
        self.message = None
        self.conversation = None
        self.artifact_host = ArtifactHost()
