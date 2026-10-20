"""
ViewSets and views for the Research app API.
"""

import io
import json
import logging
import zipfile

from asgiref.sync import sync_to_async
from django.db.models import F, Max
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from djangorestframework_camel_case.render import CamelCaseJSONRenderer
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsResearcherOrAbove
from research.api.permissions import IsResearchFeatureEnabled
from research.api.serializers import (
    ProjectMemoryDocumentSerializer,
    ResearchAgentRunDetailSerializer,
    ResearchProjectDetailSerializer,
    ResearchProjectSerializer,
    ResearchStagingItemSerializer,
    ResearchThreadSerializer,
    soul_payload,
    thread_detail,
)
from research.constants import AgentRole, ResearchSessionMode, ReviewReasonTag
from research.models import (
    ResearchAgentRun,
    ResearchKnowledgeItem,
    ResearchMemoryProposal,
    ResearchProcedure,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSession,
    ResearchStagingItem,
    ResearchThesisSource,
)
from research.services.artifact_service import request_artifact
from research.services.cancellation_service import request_run_cancellation
from research.services.chat_service import TurnStartFailed, start_turn, stream_turn
from research.services.cost_service import SpendCapReached, check_spend_cap
from research.services.critic_service import request_critic
from research.services.graph_service import build_evidence_graph
from research.services.learning_service import (
    ProposalConflict,
    ProposalInvalid,
    decide_proposal,
    learning_overview,
    prepare_profile,
    proposal_payload,
    save_project_memory,
    save_soul,
    sync_pending_learning,
)
from research.services.okf_service import (
    build_okf_bundle,
    build_okf_view,
    bundle_filename,
)
from research.services.profile_service import profile_name_for
from research.services.prompt_builder import profile_files, role_briefs
from research.services.staging_service import ReviewInvalid, review_item
from research.services.thread_service import redirect_scout, thread_has_active_turn

logger = logging.getLogger(__name__)


class ResearchAccessMixin:
    """Apply the Research Mode release flag to all research endpoints."""

    permission_classes = [
        IsAuthenticated,
        IsResearcherOrAbove,
        IsResearchFeatureEnabled,
    ]


class ResearchGenericViewSet(ResearchAccessMixin, viewsets.GenericViewSet):
    pass


class ResearchAPIView(ResearchAccessMixin, APIView):
    pass


class ResearchProjectViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    ResearchGenericViewSet,
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

    def get_serializer_class(self):
        # The single-project payload is the workspace aggregation point; it will
        # grow to nest soul file, sources, runs and staging items over time.
        if self.action == "retrieve":
            return ResearchProjectDetailSerializer
        return ResearchProjectSerializer

    def get_queryset(self):
        return ResearchProject.active_objects.filter(user=self.request.user).annotate(
            last_run_at=Max("agent_runs__created_at")
        )

    def perform_create(self, serializer):
        prepare_profile(serializer.save(user=self.request.user))


class ServerSentEventRenderer(BaseRenderer):
    """Pass-through renderer so DRF content-negotiation accepts SSE requests."""

    media_type = "text/event-stream"
    format = "txt"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


def _sse(payload):
    return f"data: {json.dumps(payload)}\n\n"


_DONE = object()


async def _async_chunks(chunks):
    """Serve a blocking generator chunk by chunk under ASGI.

    Django consumes a *sync* iterator in full before sending anything when it
    runs under ASGI, which silently turned the SSE stream into one response at
    the end. Pulling each chunk through sync_to_async keeps the ORM and the
    blocking Hermes read in a sync thread while every event flushes as it comes.
    """
    pull = sync_to_async(next)
    while True:
        chunk = await pull(chunks, _DONE)
        if chunk is _DONE:
            return
        yield chunk


class ResearchThreadListView(ResearchAPIView):
    """
    A project's chat threads — the hub of research mode.

    - GET  /api/research/projects/{id}/threads/  - threads, most recent first
    - POST /api/research/projects/{id}/threads/  - start a thread {title?}
    """

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        threads = ResearchSession.active_objects.filter(
            project=project, mode=ResearchSessionMode.CHAT
        ).order_by(F("last_run_at").desc(nulls_last=True), "-created_at")
        return Response(ResearchThreadSerializer(threads, many=True).data)

    def post(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        title = str(request.data.get("title") or "").strip()[:255]
        thread = ResearchSession.create_thread(project, request.user, title)
        return Response(
            ResearchThreadSerializer(thread).data, status=status.HTTP_201_CREATED
        )


def _owned_thread(request, thread_id):
    return get_object_or_404(
        ResearchSession.active_objects.select_related("project"),
        id=thread_id,
        mode=ResearchSessionMode.CHAT,
        project__user=request.user,
    )


class ResearchThreadView(ResearchAPIView):
    """
    - GET   /api/research/threads/{id}/  - the thread: messages and events, its
      Scouts, pending memory proposals, and whether an agent turn is live
    - PATCH /api/research/threads/{id}/  - rename {title}
    """

    def get(self, request, thread_id):
        thread = _owned_thread(request, thread_id)
        sync_pending_learning(thread.project)
        return Response(thread_detail(thread))

    def patch(self, request, thread_id):
        thread = _owned_thread(request, thread_id)
        title = str(request.data.get("title") or "").strip()[:255]
        if not title:
            return Response(
                {"error": "A non-empty 'title' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        thread.title = title
        thread.save(update_fields=["title", "updated_at"])
        return Response(ResearchThreadSerializer(thread).data)


class ResearchThreadMessageView(ResearchAPIView):
    """
    POST /api/research/threads/{id}/messages/ {message, mode?} — one scholar
    turn; streams the agent's reply as SSE: ``delta`` / ``tool`` / ``done`` /
    ``error``. ``mode: "scout"`` asks the agent to launch one Scout for the
    message straight away, when it is a research request.
    """

    renderer_classes = [CamelCaseJSONRenderer, ServerSentEventRenderer]

    def post(self, request, thread_id):
        thread = _owned_thread(request, thread_id)
        message = str(request.data.get("message") or "").strip()
        if not message:
            return Response(
                {"error": "A non-empty 'message' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if thread_has_active_turn(thread):
            return Response(
                {"error": "The agent is still replying in this thread."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            check_spend_cap(thread.project)
        except SpendCapReached as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_402_PAYMENT_REQUIRED
            )
        scout_now = request.data.get("mode") == "scout"
        try:
            run, hermes = start_turn(thread, request.user, message, scout_now=scout_now)
        except TurnStartFailed as exc:
            return Response(
                {"error": f"Could not start the agent run: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        def event_stream():
            yield _sse({"type": "started", "runId": run.id})
            for event in stream_turn(run, hermes):
                yield _sse(event)

        response = StreamingHttpResponse(
            _async_chunks(event_stream()), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class ResearchProjectMemoryDocumentView(ResearchAPIView):
    """
    The project's MEMORY.md, edited as a document.

    - GET /api/research/projects/{id}/memory/  - {entries: [...]}
    - PUT /api/research/projects/{id}/memory/  - make memory exactly these
      entries (the scholar is the author, so no proposal) and render the file
    """

    def _project(self, request, project_id):
        return get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )

    def get(self, request, project_id):
        project = self._project(request, project_id)
        return Response({"entries": ResearchProjectMemory.entries_for(project)})

    def put(self, request, project_id):
        project = self._project(request, project_id)
        serializer = ProjectMemoryDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entries = save_project_memory(
            project, serializer.validated_data["entries"], request.user
        )
        return Response({"entries": entries})


class ResearchPromptBriefsView(ResearchAPIView):
    """GET /api/research/prompts/ — every role's brief, as each run gets it."""

    def get(self, request):
        return Response({"briefs": role_briefs()})


class ResearchLearningView(ResearchAPIView):
    """
    GET /api/research/projects/{id}/learning/ — what the agent has learned and
    how: approved memory per layer, procedures, pending proposals, the decision
    timeline, and corrections per Scout run (the "is it getting better" chart).
    """

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        return Response(learning_overview(project))


class ResearchAgentRunRedirectView(ResearchAPIView):
    """
    POST /api/research/agent-runs/{id}/redirect/ {instruction} — stop a
    thread's Scout and start a replacement that follows the instruction.
    """

    def post(self, request, run_id):
        run = get_object_or_404(
            ResearchAgentRun.active_objects.select_related("thread"),
            id=run_id,
            project__user=request.user,
            role=AgentRole.SCOUT,
            thread__isnull=False,
        )
        instruction = str(request.data.get("instruction") or "").strip()[:1000]
        if not instruction:
            return Response(
                {"error": "A non-empty 'instruction' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        replacement = redirect_scout(run, instruction)
        return Response(
            {
                "runId": replacement.id,
                "replacedRunId": run.id if replacement.id != run.id else None,
                "steered": replacement.id == run.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ResearchAgentRunView(ResearchAPIView):
    """GET /api/research/agent-runs/{id}/ — one run's full audit: status, what
    it was sent, every tool call and its final reply."""

    def get(self, request, run_id):
        run = get_object_or_404(
            ResearchAgentRun.active_objects,
            id=run_id,
            project__user=request.user,
        )
        return Response(ResearchAgentRunDetailSerializer(run).data)


class ResearchAgentRunCancelView(ResearchAPIView):
    """POST an idempotent, durable cancellation request for an owned run."""

    def post(self, request, run_id):
        try:
            run, attempted = request_run_cancellation(run_id, request.user)
        except ResearchAgentRun.DoesNotExist:
            run = get_object_or_404(
                ResearchAgentRun.active_objects,
                id=run_id,
                project__user=request.user,
            )
            return Response(ResearchAgentRunDetailSerializer(run).data)

        response_status = status.HTTP_202_ACCEPTED if attempted else status.HTTP_200_OK
        return Response(
            ResearchAgentRunDetailSerializer(run).data, status=response_status
        )


class ResearchStagingItemCriticView(ResearchAPIView):
    """POST /api/research/staging-items/{id}/critic/ — queue a Critic run that
    pressure-tests the staged source; its verdict lands on criticMetadata."""

    def post(self, request, item_id):
        item = get_object_or_404(
            ResearchStagingItem.active_objects,
            id=item_id,
            project__user=request.user,
        )
        run = request_critic(item, request.user)
        return Response(
            {"runId": run.id, "status": run.status},
            status=status.HTTP_202_ACCEPTED,
        )


class ResearchArtifactGenerateView(ResearchAPIView):
    """POST /api/research/projects/{id}/artifact/ {prompt, artifactType?} —
    queue a Presenter run; the artifact lands in the project."""

    def post(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        prompt = str(request.data.get("prompt") or "").strip()
        if not prompt:
            return Response(
                {"error": "A non-empty 'prompt' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        artifact_type = str(request.data.get("artifact_type") or "").strip()
        run = request_artifact(project, request.user, prompt, artifact_type)
        return Response(
            {"runId": run.id, "status": run.status},
            status=status.HTTP_202_ACCEPTED,
        )


class ResearchProjectGraphView(ResearchAPIView):
    """
    GET /api/research/projects/{id}/graph/ — the project's evidence graph
    (nodes/edges), derived deterministically from staged sources, run
    provenance, and the gateway fetch corpus. See research.services.graph_service.
    """

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        return Response(build_evidence_graph(project), status=status.HTTP_200_OK)


class ResearchProjectOKFExportView(ResearchAPIView):
    """
    GET /api/research/projects/{id}/okf-export/ — the project's durable
    knowledge as a downloadable Open Knowledge Format (OKF v0.1) bundle: a zip
    of markdown files with YAML frontmatter. Durable layer only (promoted
    knowledge + project memory); staging is never exported. See
    research.services.okf_service.
    """

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        bundle = build_okf_bundle(project)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, content in bundle.items():
                archive.writestr(path, content)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = (
            f'attachment; filename="{bundle_filename(project)}"'
        )
        return response


class ResearchProjectOKFBundleView(ResearchAPIView):
    """
    GET /api/research/projects/{id}/okf-bundle/ — the same durable-knowledge OKF
    bundle as the zip export, but as structured JSON (ordered files with parsed
    frontmatter + markdown body, plus the source-citation link graph) for the
    in-app Maps viewer. See research.services.okf_service.build_okf_view.

    Uses the plain JSONRenderer (not the global CamelCase one) so OKF frontmatter
    keys stay verbatim — `evidence_label`, `soul_file_version` etc. are exactly
    what the exported .md files carry, and must not be camelCased.
    """

    renderer_classes = [JSONRenderer]

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        return Response(build_okf_view(project), status=status.HTTP_200_OK)


class ResearchThesisSourceLinkView(ResearchAPIView):
    """
    Manage the typed links between a thesis (ResearchProjectMemory) and the
    durable sources that bear on it — the relationship that drives the OKF
    bundle's "Supported by / Disputed by" sections.

    - GET  /api/research/theses/{memory_id}/sources/  — list links
    - POST /api/research/theses/{memory_id}/sources/  — body {sourceId, stance?};
      stance defaults to the source's own evidence label when omitted.
    """

    def _thesis(self, request, memory_id):
        return get_object_or_404(
            ResearchProjectMemory.active_objects,
            id=memory_id,
            project__user=request.user,
        )

    def get(self, request, memory_id):
        thesis = self._thesis(request, memory_id)
        links = ResearchThesisSource.active_objects.filter(thesis=thesis)
        return Response(
            [{"sourceId": link.source_id, "stance": link.stance} for link in links]
        )

    def post(self, request, memory_id):
        thesis = self._thesis(request, memory_id)
        source = get_object_or_404(
            ResearchKnowledgeItem.active_objects,
            id=request.data.get("source_id"),
            project=thesis.project,
        )
        stance = (request.data.get("stance") or "").strip()
        if not stance:
            si = source.source_staging_item
            stance = (si.evidence_label if si else "") or ""
        link, _ = ResearchThesisSource.objects.get_or_create(
            thesis=thesis,
            source=source,
            defaults={"stance": stance, "created_by": request.user},
        )
        changed = False
        if not link.is_active or link.is_deleted:
            link.is_active, link.is_deleted = True, False
            changed = True
        if stance and link.stance != stance:
            link.stance = stance
            changed = True
        if changed:
            link.save()
        return Response(
            {"sourceId": source.id, "stance": link.stance},
            status=status.HTTP_201_CREATED,
        )


class ResearchThesisSourceLinkDetailView(ResearchAPIView):
    """DELETE /api/research/theses/{memory_id}/sources/{source_id}/ — unlink."""

    def delete(self, request, memory_id, source_id):
        thesis = get_object_or_404(
            ResearchProjectMemory.active_objects,
            id=memory_id,
            project__user=request.user,
        )
        for link in ResearchThesisSource.active_objects.filter(
            thesis=thesis, source_id=source_id
        ):
            link.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ResearchAgentMemoryView(ResearchAPIView):
    """
    GET /api/research/projects/{id}/agent-memory/ — the files this project's
    agent loads on every run, exactly as DARE renders them: SOUL.md,
    MEMORY.md, USER.md and the approved skills.
    """

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        sync_pending_learning(project)
        files = profile_files(project)
        return Response(
            {
                "profile": profile_name_for(project),
                "soul": files.soul,
                "memory": files.memory,
                "user": files.user,
                "skills": [
                    {
                        "name": p.name,
                        "description": p.description,
                        "scope": "project" if p.project_id else "researcher",
                    }
                    for p in ResearchProcedure.current_for(project).order_by("name")
                ],
            }
        )


class ResearchMemoryProposalReviewView(ResearchAPIView):
    """
    POST /api/research/memory-proposals/{id}/review/ {decision, layer?}

    accept -> applied to the layer (researcher-wide, project, or — for a
    procedure — researcher-wide unless layer=project), then rendered into the
    profiles it reaches; reject -> nothing is saved.
    """

    def post(self, request, proposal_id):
        proposal = get_object_or_404(
            ResearchMemoryProposal.objects.select_related("project", "thread"),
            id=proposal_id,
            project__user=request.user,
        )
        decision = str((request.data or {}).get("decision") or "")
        layer = (request.data or {}).get("layer") or None
        try:
            decide_proposal(proposal, request.user, decision, layer=layer)
        except ProposalConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ProposalInvalid as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(proposal_payload(proposal))


class ResearchStagingItemReviewView(ResearchAPIView):
    """
    Scholar review of a staged candidate.

    POST /api/research/staging-items/{id}/review/  body {decision, reason?, tags?}
    - approve -> promote to a ResearchKnowledgeItem (the durability gate)
    - reject  -> status rejected (+ reason)
    - later   -> status later   (+ reason)

    The note and reason tags are kept on the item and, when a thread's Scout
    staged it, pushed into that thread so the agent can learn from them.
    """

    def post(self, request, item_id):
        item = get_object_or_404(
            ResearchStagingItem.active_objects.select_related("run__thread"),
            id=item_id,
            project__user=request.user,
        )
        decision = str(request.data.get("decision") or "").strip()
        note = str(request.data.get("reason") or "").strip()[:2000]
        raw_tags = request.data.get("tags") or []
        if not isinstance(raw_tags, list) or any(
            tag not in ReviewReasonTag.values() for tag in raw_tags
        ):
            return Response(
                {"error": "tags must be a list of known reason tags."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            review_item(
                item, request.user, decision, note, list(dict.fromkeys(raw_tags))
            )
        except ReviewInvalid as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ResearchStagingItemSerializer(item).data)


class ResearchSoulFileView(ResearchAPIView):
    """
    The project's SOUL.md, versioned.

    - GET /api/research/projects/{id}/soul/  - the current version
    - PUT /api/research/projects/{id}/soul/  {content, changeNote?} - save a new
      version; staged findings keep the version that governed them
    """

    def _project(self, request, project_id):
        return get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )

    def get(self, request, project_id):
        return Response(soul_payload(self._project(request, project_id)))

    def put(self, request, project_id):
        project = self._project(request, project_id)
        content = request.data.get("content")
        if not isinstance(content, str):
            return Response(
                {"error": "'content' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        save_soul(project, content, request.user, request.data.get("change_note") or "")
        return Response(soul_payload(project))
