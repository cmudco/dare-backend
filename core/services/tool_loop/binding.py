"""
The seam between the bounded tool loop and the turn that hosts it.

``ToolLoopService`` runs the same loop for every host — a chat message or a
workflow step — and reaches everything host-specific through a
``ToolLoopBinding``:

    store   persistence (tool-call rows, context trace, regeneration
            clearing) plus the object retrieval results attach to
    sink    accumulated text/thinking streamed to the host's client
    gate    the mid-stream billing check between rounds

Chat implements these over ``Message``/``MessageToolCall``
(``conversations.services.tool_loop_binding``); workflows over
``WorkflowRunStep``. The loop itself never imports either.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol, Tuple


@dataclass(frozen=True)
class ArtifactHost:
    """Where a turn's artifacts live — a chat message or a workflow step.

    Artifact tools and the MCP PDF bridge persist against exactly one host:
    chat provides ``message``+``conversation``, workflows provide
    ``workflow_run_step``. ``event_context`` carries the host's correlation
    keys (already camelCase) merged into every ``artifact_created`` /
    ``artifact_updated`` payload.
    """

    message: Optional[Any] = None
    conversation: Optional[Any] = None
    workflow_run_step: Optional[Any] = None
    event_context: Dict[str, Any] = field(default_factory=dict)

    @property
    def can_create(self) -> bool:
        chat = self.message is not None and self.conversation is not None
        return chat or self.workflow_run_step is not None


class ToolLoopStore(Protocol):
    """Host-side persistence for one tool-loop turn."""

    turn_key: str
    """Stable key naming this turn in logs and synthesized tool-call ids."""

    @property
    def retrieval_target(self) -> Any:
        """Object ``search_documents`` persists snippets/traces against."""

    async def clear_prior_tool_calls(self) -> None:
        """Drop the turn's previous tool-call rows (regeneration/re-run)."""

    async def save_context_trace(self, trace: Dict[str, Any]) -> None: ...

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
    ) -> None: ...


class ToolLoopStreamSink(Protocol):
    """Receives accumulated (not delta) text as the model streams."""

    async def text(self, accumulated_text: str) -> None: ...

    async def thinking(self, accumulated_text: str, thinking_summary: str) -> None: ...


class ToolLoopBillingGate(Protocol):
    """Mid-stream credit check; returns (can_continue, error_response)."""

    async def check(
        self, usage_totals: Dict[str, Any]
    ) -> Tuple[bool, Optional[Dict[str, Any]]]: ...


class ToolLoopBinding(Protocol):
    """Everything host-specific the loop needs, bundled."""

    store: ToolLoopStore
    sink: ToolLoopStreamSink
    gate: ToolLoopBillingGate
    correlation: Dict[str, Any]
    """Merged into every tool event payload (chat: ``{"message_id": id}``)."""
    send_callback: Callable
    user: Any
    message: Optional[Any]
    """Chat's Message, or None — tools that require chat context must
    error cleanly when absent."""
    conversation: Optional[Any]
    artifact_host: ArtifactHost
    """The host object artifact tools persist and emit against."""
