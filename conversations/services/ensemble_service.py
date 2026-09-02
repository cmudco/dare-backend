"""
Ensemble turns — a chat message answered by a panel or council of models.

The picker's line-up compiles to a hidden workflow (see
``workflows.services.ensemble_workflow_builder``): responders in one wave,
peer reviewers in the next for a council, the chairman last. The workflow
runner executes it with the chat's own context injected into every step —
history, files, memory, tools — and this module translates the run's step
events into what the chat client renders: ``deliberation`` snapshots for the
bench and ``ai_stream`` chunks for the chairman's answer. The snapshot is
persisted on the message so a refresh shows the same deliberation.

Billing happens once, on the chat message, with every participant's own
rates summed; the step handler skips its per-step billing for these runs.
"""

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional

from channels.db import database_sync_to_async

from conversations.models import Message
from conversations.services.tool_loop_binding import ChatStreamSink
from conversations.services.tool_loop_service import ToolLoopResult
from core.services.billing_service import BillingService
from core.services.dtos.builder import ARTIFACT_TOOL_SLUGS
from core.services.dtos.ensemble_dto import EnsembleRequest
from core.services.workflow_execution_service import WorkflowExecutionService
from workflows.services.ensemble_workflow_builder import (
    CHAIRMAN_NODE_ID,
    DEPTH_COUNCIL,
    ROLE_CHAIRMAN,
    ROLE_EVALUATOR,
    ROLE_RESPONDER,
    ensemble_role,
    evaluator_node_id,
    get_or_create_ensemble_workflow,
    responder_node_id,
)
from workflows.services.workflow_run_repository import WorkflowRunRepository

logger = logging.getLogger(__name__)

# A responder that overruns this is dropped so the chairman is not held
# hostage by one slow provider. The tool loop's idle timeout still catches
# stalled streams much sooner.
NODE_TIMEOUT_SECONDS = 180.0
SNAPSHOT_MIN_INTERVAL_SECONDS = 0.08


class EnsembleError(ValueError):
    """The line-up cannot run; the message carries the reason."""


@dataclass
class EnsembleTurn:
    """What a chat-driven workflow step needs to behave like a chat agent."""

    message_data: Dict[str, Any]
    user_message: str
    conversation: Any
    user: Any
    platform: Optional[str]
    # The assistant message the turn answers into; the chairman's artifacts
    # attach here so the chat shows them like any other turn's.
    message_obj: Optional[Any] = None

    def is_chairman(self, node_id: str) -> bool:
        return ensemble_role(node_id) == ROLE_CHAIRMAN

    def message_data_for(self, node_id: str) -> Dict[str, Any]:
        """The chat payload, narrowed to what this role should be able to do.

        Responders get the person's tools but never create artifacts (N models
        would make N of them). Reviewers judge on the shared evidence only.
        The chairman writes the answer and owns artifacts, but does not go
        back out to the web or MCP — that work already happened.
        """
        data = dict(self.message_data)
        data.pop("ensemble", None)
        role = ensemble_role(node_id)
        slugs = list(data.get("dare_tool_slugs") or [])
        if role == ROLE_RESPONDER:
            data["artifacts_enabled"] = False
            data["dare_tool_slugs"] = [s for s in slugs if s not in ARTIFACT_TOOL_SLUGS]
        elif role == ROLE_EVALUATOR:
            data.update(
                web_search_enabled=False,
                web_fetch_enabled=False,
                mcp_server_ids=[],
                dare_tool_slugs=[],
                artifacts_enabled=False,
                use_memory=False,
            )
        elif role == ROLE_CHAIRMAN:
            data.update(
                web_search_enabled=False, web_fetch_enabled=False, mcp_server_ids=[]
            )
            data["dare_tool_slugs"] = [s for s in slugs if s in ARTIFACT_TOOL_SLUGS]
        return data


@dataclass
class Participant:
    node_id: str
    role: str
    llm: Any
    status: str = "pending"
    text: str = ""
    started: Optional[float] = None
    ms: Optional[int] = None
    input_tokens: int = 0
    output_tokens: int = 0

    def snapshot(self, cost: Optional[str]) -> Dict[str, Any]:
        return {
            "modelId": str(self.llm.id),
            "modelName": self.llm.name,
            "provider": self.llm.provider,
            "tier": getattr(self.llm, "tier", None),
            "status": self.status,
            "text": self.text,
            "ms": self.ms,
            "cost": cost,
        }


def _parse_evaluation(text: str) -> tuple:
    """Lenient parse of the evaluator's ``{"ranking": [...], "notes": ...}``."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body.lower().startswith("json"):
            body = body[4:]
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(body[start : end + 1])
            ranking = [str(item) for item in data.get("ranking") or []]
            notes = data.get("notes")
            return ranking, (str(notes) if notes else None)
        except (ValueError, AttributeError):
            pass
    return [], (body[:200] or None)


class DeliberationTracker:
    """Turns workflow step events into deliberation snapshots and chat chunks."""

    def __init__(
        self,
        *,
        depth: str,
        participants: List[Participant],
        message_obj: Message,
        send: Callable[[Dict[str, Any]], Awaitable[None]],
        regenerate: bool,
    ) -> None:
        self.depth = depth
        self.participants = {p.node_id: p for p in participants}
        self.responders = [p for p in participants if p.role == ROLE_RESPONDER]
        self.evaluators = [p for p in participants if p.role == ROLE_EVALUATOR]
        self.chairman = self.participants[CHAIRMAN_NODE_ID]
        self.message = message_obj
        self._send = send
        self._sink = ChatStreamSink(message_obj, send, regenerate)
        self._billing = BillingService()
        self.evaluations: List[Dict[str, Any]] = []
        # Sources the responders searched, surfaced on the chat message.
        self.web_search_sources: List[Dict[str, Any]] = []
        self._seen_source_urls: set = set()
        self.workflow_run_id: Optional[int] = None
        self.chairman_context_trace: Optional[Dict[str, Any]] = None
        self.total_ms: Optional[int] = None
        self.verdict: Optional[str] = None
        self._started_at = time.monotonic()
        self._last_emit = 0.0

    # ---- workflow send_callback -------------------------------------------

    async def handle(self, payload: Dict[str, Any]) -> None:
        kind = payload.get("type")
        if isinstance(kind, str) and kind.startswith("artifact_"):
            # The chairman's artifacts already carry the chat message id.
            await self._send(payload)
            return
        participant = self.participants.get(payload.get("nodeId") or "")
        if participant is None:
            return
        if kind == "step_started":
            participant.started = time.monotonic()
        elif kind == "step_streaming":
            participant.status = "streaming"
            participant.text += payload.get("chunk") or ""
            if participant is self.chairman:
                await self._sink.text(participant.text)
            else:
                await self.emit()
        elif kind == "step_completed":
            participant.status = "done"
            participant.text = payload.get("response") or participant.text
            participant.ms = int(
                (time.monotonic() - (participant.started or self._started_at)) * 1000
            )
            tokens = payload.get("tokens") or {}
            participant.input_tokens = int(tokens.get("input") or 0)
            participant.output_tokens = int(tokens.get("output") or 0)
            if participant.role == ROLE_EVALUATOR:
                ranking, notes = _parse_evaluation(participant.text)
                self.evaluations.append(
                    {
                        "evaluatorName": participant.llm.name,
                        "ranking": ranking,
                        "notes": notes,
                    }
                )
            metadata = payload.get("metadata") or {}
            for source in metadata.get("webSearchSources") or []:
                url = source.get("url")
                if url and url not in self._seen_source_urls:
                    self._seen_source_urls.add(url)
                    self.web_search_sources.append(
                        {
                            "url": url,
                            "title": source.get("title"),
                            "cited_text": source.get("citedText"),
                            "page_age": source.get("pageAge"),
                            "provider": source.get("provider"),
                        }
                    )
            if participant is self.chairman:
                self.chairman_context_trace = metadata.get("contextTrace")
            await self.emit(force=True)
            await self.persist()
        elif kind == "step_error":
            error = (payload.get("error") or "").lower()
            participant.status = (
                "dropped" if error.startswith("timed out") else "failed"
            )
            await self.emit(force=True)
            await self.persist()

    # ---- snapshot ---------------------------------------------------------

    def _cost(self, participant: Participant) -> Decimal:
        if not (participant.input_tokens or participant.output_tokens):
            return Decimal("0")
        return self._billing._calculate_cost(
            participant.llm, participant.input_tokens, participant.output_tokens
        )

    def total_cost(self) -> Decimal:
        return sum((self._cost(p) for p in self.participants.values()), Decimal("0"))

    def snapshot(self) -> Dict[str, Any]:
        def cost_of(p: Participant) -> Optional[str]:
            return f"{self._cost(p):.6f}" if p.status == "done" else None

        return {
            "depth": self.depth,
            "responders": [p.snapshot(cost_of(p)) for p in self.responders],
            "evaluations": self.evaluations,
            "chairman": self.chairman.snapshot(cost_of(self.chairman)),
            "totalMs": self.total_ms,
            "cost": f"{self.total_cost():.6f}" if self.total_ms is not None else None,
            "verdict": self.verdict,
            "workflowRunId": self.workflow_run_id,
        }

    async def emit(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < SNAPSHOT_MIN_INTERVAL_SECONDS:
            return
        self._last_emit = now
        await self._send(
            {
                "type": "deliberation",
                "message_id": self.message.id,
                "deliberation": self.snapshot(),
            }
        )

    async def persist(self) -> None:
        snapshot = self.snapshot()
        self.message.deliberation = snapshot
        await database_sync_to_async(
            lambda: Message.active_objects.filter(id=self.message.id).update(
                deliberation=snapshot
            )
        )()

    # ---- end of turn ------------------------------------------------------

    def mark_stopped(self) -> None:
        for participant in self.participants.values():
            if participant.status in ("pending", "streaming"):
                participant.status = "dropped"

    async def finish(self) -> None:
        self.total_ms = int((time.monotonic() - self._started_at) * 1000)
        self.verdict = self._verdict()
        await self.emit(force=True)
        await self.persist()

    def _verdict(self) -> Optional[str]:
        parts = []
        dropped = sum(1 for p in self.responders if p.status in ("dropped", "failed"))
        if dropped:
            parts.append(f"{dropped} responder{'s' if dropped > 1 else ''} dropped")
        if self.depth == DEPTH_COUNCIL:
            firsts = Counter(e["ranking"][0] for e in self.evaluations if e["ranking"])
            if firsts:
                top, votes = firsts.most_common(1)[0]
                parts.append(f"peers favored {top} ({votes}/{len(self.evaluations)})")
        return " · ".join(parts) or None

    def usage_totals(self) -> Dict[str, Any]:
        input_tokens = sum(p.input_tokens for p in self.participants.values())
        output_tokens = sum(p.output_tokens for p in self.participants.values())
        totals: Dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": float(self.total_cost()),
        }
        if self.web_search_sources:
            totals["web_search_sources"] = list(self.web_search_sources)
        return totals

    def breakdown(self) -> List[Dict[str, Any]]:
        return [
            {
                "round": index,
                "input_tokens": p.input_tokens,
                "output_tokens": p.output_tokens,
                "cost": float(self._cost(p)),
                "model": p.llm.name,
                "role": p.role,
            }
            for index, p in enumerate(self.participants.values(), start=1)
        ]


class EnsembleTurnService:
    """Runs one panel or council turn and returns it in the tool loop's shape."""

    def __init__(
        self,
        *,
        send: Callable[[Dict[str, Any]], Awaitable[None]],
        resolve_descriptor: Callable[[str], Awaitable[Any]],
    ) -> None:
        self._send = send
        self._resolve_descriptor = resolve_descriptor

    async def _resolve_llm(self, model_id: str):
        descriptor = await self._resolve_descriptor(model_id)
        if descriptor is None:
            raise EnsembleError("One of the panel models was not found.")
        if descriptor.is_synthetic:
            raise EnsembleError(
                "Panels use models from the wallet catalog; LiteLLM models can't join yet."
            )
        return descriptor.llm

    async def run(
        self,
        *,
        ensemble: EnsembleRequest,
        message_data: Dict[str, Any],
        message_obj: Message,
        conversation: Any,
        user: Any,
        platform: Optional[str],
        regenerate: bool = False,
    ) -> ToolLoopResult:
        if user is None:
            raise EnsembleError("Panels are available to signed-in users only.")

        depth = ensemble.depth
        responders = [await self._resolve_llm(rid) for rid in ensemble.responder_ids]
        chairman = await self._resolve_llm(ensemble.chairman_id)

        workflow = await database_sync_to_async(get_or_create_ensemble_workflow)(
            user, depth, responders, chairman
        )
        workflow_run = await WorkflowRunRepository.create_full_run(
            workflow.id, user, user_input=message_data["message"]
        )
        if workflow_run is None:
            raise EnsembleError("The panel could not be started.")

        participants = [
            Participant(responder_node_id(i), ROLE_RESPONDER, llm)
            for i, llm in enumerate(responders, start=1)
        ]
        if depth == DEPTH_COUNCIL:
            participants += [
                Participant(evaluator_node_id(i), ROLE_EVALUATOR, llm)
                for i, llm in enumerate(responders, start=1)
            ]
        participants.append(Participant(CHAIRMAN_NODE_ID, ROLE_CHAIRMAN, chairman))

        tracker = DeliberationTracker(
            depth=depth,
            participants=participants,
            message_obj=message_obj,
            send=self._send,
            regenerate=regenerate,
        )
        tracker.workflow_run_id = workflow_run.id
        await self._link_run(message_obj, workflow_run)
        await tracker.emit(force=True)
        await tracker.persist()

        turn = EnsembleTurn(
            message_data=message_data,
            user_message=message_data["message"],
            conversation=conversation,
            user=user,
            platform=platform,
            message_obj=message_obj,
        )
        logger.info(
            "[journey] mid=%s ensemble %s: run=%s responders=%s chairman=%s",
            message_obj.id,
            depth,
            workflow_run.id,
            [llm.name for llm in responders],
            chairman.name,
        )

        try:
            await WorkflowExecutionService().execute_workflow(
                workflow_run=workflow_run,
                send_callback=tracker.handle,
                turn=turn,
                node_timeout_seconds=NODE_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            tracker.mark_stopped()
            await tracker.finish()
            await WorkflowRunRepository.mark_run_failed(workflow_run, "Stopped by user")
            return ToolLoopResult(
                text=tracker.chairman.text,
                token_usage=tracker.usage_totals(),
                usage_breakdown=tracker.breakdown(),
                cancelled=True,
            )

        await tracker.finish()
        if tracker.chairman_context_trace:
            await self._save_context_trace(message_obj, tracker.chairman_context_trace)

        return ToolLoopResult(
            text=tracker.chairman.text if tracker.chairman.status == "done" else "",
            token_usage=tracker.usage_totals(),
            usage_breakdown=tracker.breakdown(),
            rounds_used=3 if depth == DEPTH_COUNCIL else 2,
        )

    @staticmethod
    @database_sync_to_async
    def _link_run(message_obj: Message, workflow_run) -> None:
        message_obj.workflow_run = workflow_run
        Message.active_objects.filter(id=message_obj.id).update(
            workflow_run=workflow_run
        )

    @staticmethod
    @database_sync_to_async
    def _save_context_trace(message_obj: Message, trace: Dict[str, Any]) -> None:
        message_obj.context_trace = trace
        Message.active_objects.filter(id=message_obj.id).update(context_trace=trace)
