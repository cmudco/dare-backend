"""
ViewSets and views for the Research app API.
"""

import io
import json
import logging
import zipfile

from asgiref.sync import sync_to_async
from django.db.models import F
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
    ResearchAgentRunSerializer,
    ResearcherStandardsSerializer,
    ResearchProjectDetailSerializer,
    ResearchProjectSerializer,
    ResearchStagingItemSerializer,
    ResearchThreadSerializer,
    thread_detail,
)
from research.constants import (
    AgentRunStatus,
    MemoryProposalStatus,
    ResearchSessionMode,
    ReviewReasonTag,
    SoulFileOrigin,
    StagingItemStatus,
)
from research.models import (
    ResearchAgentMemorySnapshot,
    ResearchAgentRun,
    ResearcherProfile,
    ResearchKnowledgeItem,
    ResearchMemoryProposal,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSession,
    ResearchStagingItem,
    ResearchThesisSource,
    SoulFile,
    SoulFileVersion,
)
from research.services import get_hermes_service, request_run_cancellation
from research.services.chat_service import TurnStartFailed, start_turn, stream_turn
from research.services.cost_service import SpendCapReached, check_spend_cap
from research.services.graph_service import build_evidence_graph
from research.services.learning_service import (
    ProposalConflict,
    ProposalInvalid,
    decide_proposal,
    learning_overview,
    project_memory_entries,
    proposal_payload,
    save_project_memory,
    save_researcher_standards,
    sync_pending_learning,
)
from research.services.okf_service import (
    build_okf_bundle,
    build_okf_view,
    bundle_filename,
)
from research.services.thread_service import (
    record_review_event,
    redirect_scout,
    thread_has_active_turn,
)
from research.tasks import run_artifact_job, run_critic_job

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
        return ResearchProject.active_objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        project = serializer.save(user=self.request.user)
        # Write the profile's files now — SOUL.md, MEMORY.md, USER.md — so the
        # agent files match what the scholar just saved before any run starts.
        hermes = get_hermes_service(project)
        hermes.provision_soul(project.soul_file.current_version().content)


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
        if not thread.title:
            thread.title = message[:80]
            thread.save(update_fields=["title", "updated_at"])
        scout_now = request.data.get("mode") == "scout"
        try:
            check_spend_cap(thread.project)
        except SpendCapReached as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_402_PAYMENT_REQUIRED
            )
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
        return Response({"entries": project_memory_entries(project)})

    def put(self, request, project_id):
        project = self._project(request, project_id)
        serializer = ProjectMemoryDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entries = save_project_memory(
            project, serializer.validated_data["entries"], request.user
        )
        return Response({"entries": entries})


class ResearcherStandardsView(ResearchAPIView):
    """
    The scholar's researcher-wide standards — what every project's agent
    follows about how they work. Written directly by the scholar (no proposal:
    they are the author), rendered into USER.md of every project they own.

    - GET /api/research/researcher/standards/  - {standards: [...]}
    - PUT /api/research/researcher/standards/  - replace the list
    """

    def get(self, request):
        profile = ResearcherProfile.active_objects.filter(user=request.user).first()
        return Response({"standards": profile.entries() if profile else []})

    def put(self, request):
        serializer = ResearcherStandardsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        standards = save_researcher_standards(
            request.user, serializer.validated_data["standards"]
        )
        return Response({"standards": standards})


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
            role="scout",
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
    """GET /api/research/agent-runs/{id}/ — a run's live status (for polling)
    and full run-details audit payload (adds the agent's exact final response)."""

    # Terminal states whose final output is worth backfilling from Hermes.
    _BACKFILLABLE = {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.OUTCOME_UNKNOWN,
    }

    def get(self, request, run_id):
        run = get_object_or_404(
            ResearchAgentRun.active_objects,
            id=run_id,
            project__user=request.user,
        )
        if (
            not run.raw_output
            and run.hermes_run_id
            and run.status in self._BACKFILLABLE
        ):
            self._backfill_raw_output(run)
        return Response(ResearchAgentRunDetailSerializer(run).data)

    @staticmethod
    def _backfill_raw_output(run):
        """Best-effort pull of the final response from Hermes for runs that
        predate raw_output persistence, while its run record is still retained.
        Absence stays honest — the record may have expired (Hermes TTL)."""
        try:
            terminal = get_hermes_service(run.project).get_run(run.hermes_run_id)
        except Exception:  # noqa: BLE001 - absence is an honest state, not an error
            return
        output = terminal.get("output")
        if isinstance(output, str) and output.strip():
            run.raw_output = output
            run.save(update_fields=["raw_output", "updated_at"])


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
            return Response(ResearchAgentRunSerializer(run).data)

        response_status = status.HTTP_202_ACCEPTED if attempted else status.HTTP_200_OK
        return Response(ResearchAgentRunSerializer(run).data, status=response_status)


class ResearchStagingItemCriticView(ResearchAPIView):
    """
    POST /api/research/staging-items/{id}/critic/ — enqueue a Critic run that
    pressure-tests the staged source against the standards. The verdict lands on
    the item's criticMetadata. Returns {runId}; poll GET /agent-runs/{id}/.
    """

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


class ResearchArtifactGenerateView(ResearchAPIView):
    """
    POST /api/research/projects/{id}/artifact/ {prompt, artifactType?} — enqueue a
    delegated run that produces a renderable artifact via the JSON contract.
    Returns {runId}; poll GET /agent-runs/{id}/. The artifact lands in the project.
    """

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
            id=request.data.get("sourceId") or request.data.get("source_id"),
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


def _record_and_diff_memory(project, files, keep=12):
    """Snapshot this project's memory if it changed, and return its recent history.

    The runtime edits MEMORY.md and USER.md in place, so without this the files
    can only say what the agent believes *now*. Snapshotting on read is enough to
    reconstruct how they got there, and it stays off the run path — a slow or
    failing snapshot can never break a chat.

    Each entry reports what changed against the snapshot before it, so the
    workspace can show "the agent learned X on Tuesday" rather than a wall of
    current text.
    """
    latest = (
        ResearchAgentMemorySnapshot.active_objects.filter(project=project)
        .order_by("-created_at")
        .first()
    )
    if latest is None or not latest.matches(files):
        # Only when the text actually moved, so an idle project stores nothing.
        latest = ResearchAgentMemorySnapshot.objects.create(
            project=project,
            soul=files.get("soul", ""),
            memory=files.get("memory", ""),
            user=files.get("user", ""),
        )

    snaps = list(
        ResearchAgentMemorySnapshot.active_objects.filter(project=project).order_by(
            "-created_at"
        )[:keep]
    )
    history = []
    for index, snap in enumerate(snaps):
        older = snaps[index + 1] if index + 1 < len(snaps) else None
        entry = {"id": snap.id, "takenAt": snap.created_at}
        for field in ("memory", "user"):
            now = ResearchAgentMemorySnapshot.entries(getattr(snap, field))
            before = (
                ResearchAgentMemorySnapshot.entries(getattr(older, field))
                if older
                else []
            )
            entry[field] = {
                "count": len(now),
                "added": [e for e in now if e not in before],
                "removed": [e for e in before if e not in now],
            }
        entry["isFirst"] = older is None
        history.append(entry)
    return history


class ResearchAgentMemoryView(ResearchAPIView):
    """
    GET /api/research/projects/{id}/agent-memory/ — the operational files
    (SOUL.md, MEMORY.md, USER.md) of THIS project's Hermes profile, read-only,
    with their change history. DARE renders MEMORY.md and USER.md from what the
    scholar approved, so these show exactly what the agent is working from.
    """

    def get(self, request, project_id):
        project = get_object_or_404(
            ResearchProject.active_objects, id=project_id, user=request.user
        )
        sync_pending_learning(project)
        files = get_hermes_service(project).read_agent_memory()
        files["history"] = _record_and_diff_memory(project, files)
        files["discarded"] = list(
            ResearchMemoryProposal.active_objects.filter(
                project=project, status=MemoryProposalStatus.REJECTED
            ).values_list("content", flat=True)
        )
        return Response(files)


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
        decision = (request.data.get("decision") or "").strip()
        reason = str(request.data.get("reason") or "").strip()[:2000]
        raw_tags = request.data.get("tags") or []
        if not isinstance(raw_tags, list) or any(
            tag not in ReviewReasonTag.values() for tag in raw_tags
        ):
            return Response(
                {"error": "tags must be a list of known reason tags."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tags = list(dict.fromkeys(raw_tags))

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

        item.review_metadata = {
            **(item.review_metadata or {}),
            "decision": decision,
            "note": reason,
            "tags": tags,
            "decidedAt": timezone.now().isoformat(),
        }
        item.save(update_fields=["review_metadata", "updated_at"])
        record_review_event(item, decision, reason, tags)
        return Response(ResearchStagingItemSerializer(item).data)


class ResearchSoulFileView(ResearchAPIView):
    """
    The project's versioned soul file (standards).

    - GET /api/research/projects/{id}/soul/  - the current version
    - PUT /api/research/projects/{id}/soul/  {content, changeNote?} - write a new
      version (the old one is kept; staging items keep the version that governed
      them).
    """

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
        # Keep THIS project's profile SOUL.md in sync with the new version.
        get_hermes_service(project).provision_soul(content)
        return Response(self._serialize(soul))
