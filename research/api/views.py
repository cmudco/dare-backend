"""
ViewSets and views for the Research app API.
"""

import json
import logging

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from djangorestframework_camel_case.render import CamelCaseJSONRenderer
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsResearcherOrAbove
from research.api.serializers import (
    ResearchAgentRunSerializer,
    ResearchChatMessageSerializer,
    ResearchProjectDetailSerializer,
    ResearchProjectSerializer,
    ResearchStagingItemSerializer,
)
from research.constants import (
    AgentRunStatus,
    AgentToolCallStatus,
    ChatMessageRole,
    ResearchSessionMode,
    SoulFileOrigin,
    StagingItemStatus,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchChatMessage,
    ResearchKnowledgeItem,
    ResearchProject,
    ResearchSession,
    ResearchStagingItem,
    SoulFile,
    SoulFileVersion,
)
from research.services import get_hermes_service
from research.services.graph_service import build_evidence_graph
from research.tasks import (
    _knowledge_block,
    _match_gateway_fetch,
    _project_memory_block,
    run_artifact_job,
    run_critic_job,
    run_scout_job,
)

logger = logging.getLogger(__name__)


class ResearchProjectViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Research projects owned by the authenticated researcher.

    Endpoints:
    - GET   /api/research/projects/       - list the user's projects
    - POST  /api/research/projects/       - create a project
    - GET   /api/research/projects/{id}/  - retrieve a single project
    - PATCH /api/research/projects/{id}/  - update a project
    """

    serializer_class = ResearchProjectSerializer
    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def get_serializer_class(self):
        # The single-project payload is the workspace aggregation point; it will
        # grow to nest soul file, sources, runs and staging items over time.
        if self.action == "retrieve":
            return ResearchProjectDetailSerializer
        return ResearchProjectSerializer

    def get_queryset(self):
        return ResearchProject.active_objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ServerSentEventRenderer(BaseRenderer):
    """Pass-through renderer so DRF content-negotiation accepts SSE requests."""

    media_type = "text/event-stream"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


def _sse(payload):
    return f"data: {json.dumps(payload)}\n\n"


# Standing chat framing, layered over the soul file: chat is for thinking, the
# Artifacts tab is for producing — large payloads don't belong in a transcript.
CHAT_BRIEF = (
    "You are the project's research assistant in hands-on chat — think live "
    "with the scholar under the standards above. If they ask you to produce a "
    "renderable artifact (a diagram, deck, document, or figure), give at most "
    "a brief sketch of what it could contain and point them to the Artifacts "
    "tab, which generates and saves artifacts properly — do not paste large "
    "artifact payloads into the chat. "
    "Be honest about tool failures: if a tool errors, tell the scholar plainly "
    "what failed and why (quota exhausted, auth, not found, blocked/paywalled) "
    "instead of guessing or blaming the wrong layer, and do not retry a tool "
    "that returned a quota or auth error — switch approaches or say what you need."
)


def _recent_transcript(session, max_turns=12, max_chars=6000):
    """The running chat transcript (prior turns) so the agent has verbatim memory
    of THIS conversation. DARE owns the history, so we replay it rather than trust
    the runtime's session summary, which drops specifics (the amnesia root cause)."""
    msgs = list(
        ResearchChatMessage.active_objects.filter(session=session).order_by(
            "-created_at"
        )[:max_turns]
    )
    msgs.reverse()
    lines = [
        f"{'Scholar' if m.role == ChatMessageRole.USER else 'You'}: {m.content}"
        for m in msgs
    ]
    return "\n\n".join(lines)[-max_chars:]


def _chat_instructions(project, soul_content, history=""):
    """Soul + chat framing + project context + the running conversation transcript."""
    parts = [soul_content] if soul_content else []
    parts.append(CHAT_BRIEF)
    context = []
    if project.question and project.question.strip():
        context.append(
            "The project's research question (what this project is about): "
            + project.question.strip()
        )
    else:
        context.append(
            "This project has no research question set yet. If the scholar's "
            "message implies a research topic, help them articulate and sharpen "
            "it into one — never claim you have no idea what the project is about."
        )
    knowledge = _knowledge_block(project)
    if knowledge:
        context.append(f"The scholar's approved project knowledge so far:\n{knowledge}")
    if context:
        parts.append("# Project context\n" + "\n\n".join(context))
    memory = _project_memory_block(project)
    if memory:
        parts.append(
            "# Project memory (durable, DARE-owned — your persistent memory for "
            "THIS project across sessions; rely on it)\n" + memory
        )
    if history:
        parts.append(
            "# Conversation so far (oldest first, most recent last)\n"
            "This is the running transcript of THIS chat — it is your memory of the "
            "conversation. Refer to it directly; never claim the chat just began.\n\n"
            + history
        )
    return "\n\n".join(parts)


class ResearchChatView(APIView):
    """
    Hands-on chat for a project — one persistent chat session, backed by Hermes.

    - GET  /api/research/projects/{id}/chat/  - the chat transcript
    - POST /api/research/projects/{id}/chat/  - send a message; streams the
      assistant reply back as SSE (proxying Hermes `message.delta` events).
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]
    renderer_classes = [CamelCaseJSONRenderer, ServerSentEventRenderer]

    def _get_project(self, request, project_id):
        return get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )

    def get(self, request, project_id):
        project = self._get_project(request, project_id)
        session = ResearchSession.active_objects.filter(
            project=project, mode=ResearchSessionMode.CHAT
        ).first()
        messages = (
            ResearchChatMessage.active_objects.filter(session=session).order_by(
                "created_at"
            )
            if session
            else ResearchChatMessage.objects.none()
        )
        return Response(ResearchChatMessageSerializer(messages, many=True).data)

    def post(self, request, project_id):
        project = self._get_project(request, project_id)
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response(
                {"error": "A non-empty 'message' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = ResearchSession.get_or_create_chat_session(project, request.user)
        # Capture the prior turns BEFORE recording this new message, so the agent
        # gets verbatim conversation memory (fixes the "this chat just began" amnesia).
        history = _recent_transcript(session)
        soul = SoulFile.active_objects.filter(project=project).first()
        soul_version = soul.current_version() if soul else None
        soul_content = soul_version.content if soul_version else ""
        soul_label = f"v{soul_version.version}" if soul_version else ""

        run = ResearchAgentRun.objects.create(
            session=session,
            project=project,
            user=request.user,
            role="main-assistant",
            mode=ResearchSessionMode.CHAT,
            task=message,
            status=AgentRunStatus.RUNNING,
            soul_file_version=soul_label,
            started_at=timezone.now(),
        )
        ResearchChatMessage.objects.create(
            session=session,
            project=project,
            user=request.user,
            role=ChatMessageRole.USER,
            content=message,
        )

        hermes = get_hermes_service(project)
        # Anchor: write DARE's soul into the gateway SOUL.md (read fresh each
        # run); instructions remain a resilient fallback overlay.
        hermes.provision_soul(soul_content)
        try:
            started = hermes.start_run(
                input_text=message,
                instructions=_chat_instructions(project, soul_content, history=history),
                session_id=session.hermes_session_id,
                session_key=f"dare-proj{project.id}",
            )
            hermes_run_id = started["run_id"]
            logger.info(
                "research.chat run %s started: project=%s session=%s "
                "prior_turns=%s msg_chars=%s hermes_run=%s",
                run.id,
                project.id,
                session.id,
                (history.count("\n\n") + 1 if history else 0),
                len(message),
                hermes_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a failed run
            logger.error("research.chat run %s start_run failed: %s", run.id, exc)
            run.status = AgentRunStatus.FAILED
            run.error = str(exc)
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "error", "completed_at", "updated_at"])
            return Response(
                {"error": f"Could not start the agent run: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        run.hermes_run_id = hermes_run_id
        run.save(update_fields=["hermes_run_id", "updated_at"])

        def event_stream():
            chunks = []
            try:
                for event in hermes.stream_events(hermes_run_id):
                    event_type = event.get("event")
                    if event_type == "message.delta":
                        delta = event.get("delta", "")
                        if delta:
                            chunks.append(delta)
                            yield _sse({"type": "delta", "delta": delta})
                    elif event_type == "tool.started":
                        yield _sse(
                            {
                                "type": "tool",
                                "phase": "started",
                                "tool": event.get("tool", ""),
                            }
                        )
                    elif event_type == "tool.completed":
                        duration = event.get("duration")
                        tool_name = event.get("tool", "")
                        raw_error = event.get("error")
                        is_error = bool(raw_error)
                        # SSE flags failure as a boolean; the gateway captured the
                        # real reason (paywall/auth) and the result body — link both.
                        error_text = raw_error if isinstance(raw_error, str) else ""
                        result_summary = ""
                        if is_error and not error_text:
                            failed = _match_gateway_fetch(
                                run, tool_name, want_error=True
                            )
                            if failed:
                                error_text = failed.error
                        elif not is_error:
                            fetch = _match_gateway_fetch(run, tool_name)
                            if fetch:
                                result_summary = fetch.content[:2000]
                        ResearchAgentToolCall.objects.create(
                            run=run,
                            tool=tool_name,
                            status=(
                                AgentToolCallStatus.ERROR
                                if is_error
                                else AgentToolCallStatus.SUCCESS
                            ),
                            duration_ms=(int(duration * 1000) if duration else None),
                            result_summary=result_summary,
                            error=error_text,
                        )
                        logger.info(
                            "research.chat run %s tool %s -> %s%s",
                            run.id,
                            tool_name,
                            "error" if is_error else "ok",
                            f" ({error_text[:120]})" if error_text else "",
                        )
                        yield _sse(
                            {
                                "type": "tool",
                                "phase": "completed",
                                "tool": event.get("tool", ""),
                            }
                        )
                    elif event_type == "run.completed":
                        break
            except Exception as exc:  # noqa: BLE001 - mark the run failed
                logger.error(
                    "research.chat run %s stream failed: %s",
                    run.id,
                    exc,
                    exc_info=True,
                )
                run.status = AgentRunStatus.FAILED
                run.error = str(exc)
                run.completed_at = timezone.now()
                run.save(
                    update_fields=["status", "error", "completed_at", "updated_at"]
                )
                yield _sse(
                    {"type": "error", "error": f"The agent stream failed: {exc}"}
                )
                return

            assistant_message = ResearchChatMessage.objects.create(
                session=session,
                project=project,
                user=request.user,
                role=ChatMessageRole.ASSISTANT,
                content="".join(chunks),
                run=run,
            )
            run.status = AgentRunStatus.COMPLETED
            run.completed_at = timezone.now()
            run.usage = hermes.fetch_usage(hermes_run_id)
            run.save(update_fields=["status", "completed_at", "usage", "updated_at"])
            logger.info(
                "research.chat run %s completed: reply_chars=%s usage=%s",
                run.id,
                len("".join(chunks)),
                run.usage,
            )
            session.last_run_at = timezone.now()
            session.save(update_fields=["last_run_at", "updated_at"])
            yield _sse(
                {
                    "type": "done",
                    "messageId": assistant_message.id,
                    "runId": run.id,
                }
            )

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class ResearchScoutView(APIView):
    """
    Delegated Scout discovery for a project.

    POST /api/research/projects/{id}/scout/ — creates a run and enqueues a
    background job (runs are long: web search + synthesis), returning immediately
    with the run id. The client polls GET /api/research/agent-runs/{id}/ for live
    status; staged findings land in the Review Inbox when the run completes.
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def post(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        task = (request.data.get("task") or request.data.get("query") or "").strip()
        if not task:
            return Response(
                {"error": "A non-empty 'task' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # 'quick' caps the search/read budget (the token-cost lever); anything
        # else gets the default deep run.
        depth = "quick" if request.data.get("depth") == "quick" else "deep"
        # Per-run tool selection from the composer; sanitized against the
        # project's enabled set (a run can narrow the toolset, never widen it).
        requested = request.data.get("tools")
        tools = (
            [t for t in requested if isinstance(t, str) and t in project.enabled_tools]
            if isinstance(requested, list)
            else list(project.enabled_tools)
        )

        session = ResearchSession.get_or_create_scout_session(project, request.user)
        soul = SoulFile.active_objects.filter(project=project).first()
        version = soul.current_version() if soul else None
        soul_label = f"v{version.version}" if version else ""

        run = ResearchAgentRun.objects.create(
            session=session,
            project=project,
            user=request.user,
            role="scout",
            mode=ResearchSessionMode.SCOUT,
            task=task,
            status=AgentRunStatus.RUNNING,
            status_detail="Queued…",
            soul_file_version=soul_label,
            allowed_tools=tools or ["web"],
            selected_context={"depth": depth, "tools": tools},
            started_at=timezone.now(),
        )
        run_scout_job.delay(run.id)
        return Response(
            {"runId": run.id, "status": run.status},
            status=status.HTTP_202_ACCEPTED,
        )


class ResearchAgentRunView(APIView):
    """GET /api/research/agent-runs/{id}/ — a run's live status (for polling)."""

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def get(self, request, run_id):
        run = get_object_or_404(
            ResearchAgentRun.active_objects,
            id=run_id,
            project__user=request.user,
        )
        return Response(ResearchAgentRunSerializer(run).data)


class ResearchStagingItemCriticView(APIView):
    """
    POST /api/research/staging-items/{id}/critic/ — enqueue a Critic run that
    pressure-tests the staged source against the standards. The verdict lands on
    the item's criticMetadata. Returns {runId}; poll GET /agent-runs/{id}/.
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def post(self, request, item_id):
        item = get_object_or_404(
            ResearchStagingItem.active_objects,
            id=item_id,
            project__user=request.user,
        )
        project = item.project
        session = ResearchSession.get_or_create_scout_session(project, request.user)
        soul = SoulFile.active_objects.filter(project=project).first()
        version = soul.current_version() if soul else None
        soul_label = f"v{version.version}" if version else ""

        run = ResearchAgentRun.objects.create(
            session=session,
            project=project,
            user=request.user,
            role="critic",
            mode=ResearchSessionMode.SCOUT,
            task=f"Pressure-test: {item.title}",
            status=AgentRunStatus.RUNNING,
            status_detail="Queued…",
            soul_file_version=soul_label,
            allowed_tools=["web"],
            started_at=timezone.now(),
        )
        run_critic_job.delay(run.id, item.id)
        return Response(
            {"runId": run.id, "status": run.status},
            status=status.HTTP_202_ACCEPTED,
        )


class ResearchArtifactGenerateView(APIView):
    """
    POST /api/research/projects/{id}/artifact/ {prompt, artifactType?} — enqueue a
    delegated run that produces a renderable artifact via the JSON contract.
    Returns {runId}; poll GET /agent-runs/{id}/. The artifact lands in the project.
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def post(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        prompt = (request.data.get("prompt") or "").strip()
        if not prompt:
            return Response(
                {"error": "A non-empty 'prompt' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        artifact_type = (
            request.data.get("artifact_type") or request.data.get("artifactType") or ""
        ).strip()

        session = ResearchSession.get_or_create_artifact_session(project, request.user)
        soul = SoulFile.active_objects.filter(project=project).first()
        version = soul.current_version() if soul else None
        soul_label = f"v{version.version}" if version else ""

        run = ResearchAgentRun.objects.create(
            session=session,
            project=project,
            user=request.user,
            role="presenter",
            mode=ResearchSessionMode.ARTIFACT,
            task=prompt,
            status=AgentRunStatus.RUNNING,
            status_detail="Queued…",
            soul_file_version=soul_label,
            allowed_tools=["skills"],
            selected_context={"artifactType": artifact_type},
            started_at=timezone.now(),
        )
        run_artifact_job.delay(run.id)
        return Response(
            {"runId": run.id, "status": run.status},
            status=status.HTTP_202_ACCEPTED,
        )


class ResearchProjectGraphView(APIView):
    """
    GET /api/research/projects/{id}/graph/ — the project's evidence graph
    (nodes/edges), derived deterministically from staged sources, run
    provenance, and the gateway fetch corpus. See research.services.graph_service.
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        return Response(build_evidence_graph(project), status=status.HTTP_200_OK)


class ResearchAgentMemoryView(APIView):
    """
    GET /api/research/agent-memory/ — the Hermes profile's operational memory
    files (SOUL.md, MEMORY.md, USER.md), read-only, for the Agent Memory view.
    SOUL.md mirrors the project soul DARE provisions; MEMORY/USER are what Hermes
    auto-writes as it learns.
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def get(self, request):
        return Response(get_hermes_service().read_agent_memory())


class ResearchStagingItemReviewView(APIView):
    """
    Scholar review of a staged candidate.

    POST /api/research/staging-items/{id}/review/  body {decision, reason?}
    - approve -> promote to a ResearchKnowledgeItem (the durability gate)
    - reject  -> status rejected (+ reason)
    - later   -> status later   (+ reason)
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def post(self, request, item_id):
        item = get_object_or_404(
            ResearchStagingItem.active_objects,
            id=item_id,
            project__user=request.user,
        )
        decision = (request.data.get("decision") or "").strip()
        reason = (request.data.get("reason") or "").strip()

        if decision == "approve":
            ResearchKnowledgeItem.objects.create(
                project=item.project,
                source_staging_item=item,
                approved_by=request.user,
                approved_at=timezone.now(),
                content=item.content or item.rationale,
                rationale=item.rationale,
                provenance=item.provenance,
                soul_file_version=str(
                    (item.provenance or {}).get("soulFileVersion") or ""
                ),
            )
            item.status = StagingItemStatus.APPROVED
            item.save(update_fields=["status", "updated_at"])
        elif decision == "reject":
            item.status = StagingItemStatus.REJECTED
            item.rejection_reason = reason
            item.save(update_fields=["status", "rejection_reason", "updated_at"])
        elif decision == "later":
            item.status = StagingItemStatus.LATER
            item.later_reason = reason
            item.save(update_fields=["status", "later_reason", "updated_at"])
        else:
            return Response(
                {"error": "decision must be one of: approve, reject, later."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(ResearchStagingItemSerializer(item).data)


class ResearchSoulFileView(APIView):
    """
    The project's versioned soul file (standards).

    - GET /api/research/projects/{id}/soul/  - the current version
    - PUT /api/research/projects/{id}/soul/  {content, changeNote?} - write a new
      version (the old one is kept; staging items keep the version that governed
      them).
    """

    permission_classes = [IsAuthenticated, IsResearcherOrAbove]

    def _project(self, request, project_id):
        return get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )

    def _serialize(self, soul):
        version = soul.current_version() if soul else None
        if not version:
            return None
        return {
            "id": soul.id,
            "version": version.version,
            "content": version.content,
            "origin": version.origin,
            "updatedAt": version.created_at,
        }

    def get(self, request, project_id):
        project = self._project(request, project_id)
        soul = SoulFile.active_objects.filter(project=project).first()
        return Response(self._serialize(soul))

    def put(self, request, project_id):
        project = self._project(request, project_id)
        content = request.data.get("content")
        if content is None:
            return Response(
                {"error": "'content' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        change_note = request.data.get("change_note") or ""
        soul, _ = SoulFile.objects.get_or_create(project=project)
        latest = (
            SoulFileVersion.active_objects.filter(soul_file=soul)
            .order_by("-version")
            .first()
        )
        SoulFileVersion.objects.create(
            soul_file=soul,
            version=(latest.version + 1) if latest else 1,
            content=content,
            origin=SoulFileOrigin.EDIT,
            change_note=change_note,
            created_by=request.user,
        )
        # Keep the Hermes profile SOUL.md in sync with the new version.
        get_hermes_service().provision_soul(content)
        return Response(self._serialize(soul))
