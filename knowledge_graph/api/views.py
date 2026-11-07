from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_rq import enqueue, get_connection
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from core.services.rag import build_pipeline
from core.services.rag.dtos import RetrievalRequest
from files.models import File, Folder
from knowledge_graph.api.serializers import (
    ApproveSerializer,
    AskSerializer,
    DiscoverSerializer,
    GraphCreateSerializer,
    GraphUpdateSerializer,
    LabelWriteSerializer,
    MergeSerializer,
    SeparateSerializer,
)
from knowledge_graph.constants import MAX_RELATION_LABELS, GraphStatus, LabelSource
from knowledge_graph.models import EntityCluster, KnowledgeGraph, RelationLabel
from knowledge_graph.services.cluster_service import EntityClusterService
from knowledge_graph.services.graph_view_service import GraphViewService
from knowledge_graph.tasks import (
    build_graph_relations,
    discover_graph_labels,
    prepare_graph,
)
from projects.services.project_service import selected_source_files

BUSY_STATUSES = (GraphStatus.PREPARING, GraphStatus.DISCOVERING, GraphStatus.BUILDING)
ASK_TOP_K = 4


class KnowledgeGraphViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def _graph(self, pk) -> KnowledgeGraph:
        return get_object_or_404(KnowledgeGraph, pk=pk, user=self.request.user)

    def _cluster(self, cluster_id) -> EntityCluster:
        return get_object_or_404(EntityCluster, pk=cluster_id, user=self.request.user)

    def list(self, request):
        graphs = KnowledgeGraph.objects.filter(user=request.user)
        return Response([GraphViewService.summary(graph) for graph in graphs])

    def create(self, request):
        body = GraphCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        file_ids = set(body.validated_data["file_ids"])
        folder_ids = set(body.validated_data["folder_ids"])
        owned_files = File.active_objects.filter(user=request.user, id__in=file_ids)
        if owned_files.count() != len(file_ids):
            raise ValidationError({"file_ids": "Some files could not be found."})
        owned_folders = Folder.objects.filter(user=request.user, id__in=folder_ids)
        if owned_folders.count() != len(folder_ids):
            raise ValidationError({"folder_ids": "Some folders could not be found."})
        files = list(selected_source_files(request.user, file_ids, folder_ids))
        if not files:
            raise ValidationError({"file_ids": "Choose at least one document."})
        graph = KnowledgeGraph.objects.create(
            user=request.user,
            name=body.validated_data["name"],
            description=body.validated_data["description"],
        )
        graph.files.set(files)
        response = self._start(graph, GraphStatus.PREPARING, prepare_graph)
        response.status_code = status.HTTP_201_CREATED
        return response

    def retrieve(self, request, pk=None):
        return Response(GraphViewService.summary(self._graph(pk)))

    def partial_update(self, request, pk=None):
        graph = self._graph(pk)
        body = GraphUpdateSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        for field, value in body.validated_data.items():
            setattr(graph, field, value)
        graph.save()
        return Response(GraphViewService.summary(graph))

    def destroy(self, request, pk=None):
        self._graph(pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="view")
    def graph_view(self, request, pk=None):
        focus = request.query_params.get("focus", "")
        return Response(
            GraphViewService.view(
                self._graph(pk), focus=int(focus) if focus.isdigit() else None
            )
        )

    @action(detail=True, methods=["get"], url_path="estimate")
    def estimate(self, request, pk=None):
        return Response(GraphViewService.estimate(self._graph(pk)))

    @action(detail=True, methods=["get"], url_path="eval-report")
    def eval_report(self, request, pk=None):
        return Response({"report": self._graph(pk).eval_report})

    @action(detail=True, methods=["post"], url_path="ask")
    def ask(self, request, pk=None):
        """Run retrieval only, so the page can draw the path a question takes."""
        graph = self._graph(pk)
        body = AskSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        file_ids = tuple(
            graph.files.filter(is_deleted=False, is_active=True).values_list(
                "id", flat=True
            )
        )
        result = build_pipeline("document").run(
            RetrievalRequest(
                query=body.validated_data["question"],
                top_k=ASK_TOP_K,
                file_ids=file_ids,
                user_id=request.user.id,
                payer_user_id=request.user.id,
                trace=True,
            )
        )
        return Response({"trace": result.trace.to_payload()})

    @staticmethod
    def _job_is_alive(graph: KnowledgeGraph) -> bool:
        """A worker that dies mid-job leaves the status behind; the queue knows better."""
        try:
            job = Job.fetch(graph.job_id, connection=get_connection("default"))
        except NoSuchJobError:
            return False
        return job.get_status() in (
            JobStatus.QUEUED,
            JobStatus.STARTED,
            JobStatus.DEFERRED,
            JobStatus.SCHEDULED,
        )

    def _start(
        self, graph: KnowledgeGraph, next_status: str, job_function, *job_args
    ) -> Response:
        if graph.status in BUSY_STATUSES and self._job_is_alive(graph):
            return Response(
                {"error": "This graph is already working."},
                status=status.HTTP_409_CONFLICT,
            )
        graph.status = next_status
        graph.error = ""
        if next_status == GraphStatus.BUILDING:
            graph.approved_at = timezone.now()
        graph.save()
        job = enqueue(job_function, graph.id, *job_args)
        graph.job_id = job.id
        graph.save(update_fields=["job_id", "updated_at"])
        return Response(GraphViewService.summary(graph))

    @action(detail=True, methods=["post"], url_path="discover")
    def discover(self, request, pk=None):
        graph = self._graph(pk)
        body = DiscoverSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        if "goal" in body.validated_data:
            graph.goal = body.validated_data["goal"].strip()
        return self._start(graph, GraphStatus.DISCOVERING, discover_graph_labels)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        graph = self._graph(pk)
        if not graph.labels.exists():
            raise ValidationError({"labels": "Add at least one relationship type."})
        body = ApproveSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self._start(
            graph,
            GraphStatus.BUILDING,
            build_graph_relations,
            body.validated_data.get("new_only", False),
        )

    @action(detail=True, methods=["post"], url_path="labels")
    def add_label(self, request, pk=None):
        graph = self._graph(pk)
        body = LabelWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        name = body.validated_data.get("name")
        if not name:
            raise ValidationError({"name": "Enter a name."})
        if graph.labels.count() >= MAX_RELATION_LABELS:
            raise ValidationError(
                {"name": f"A graph can have {MAX_RELATION_LABELS} types."}
            )
        if graph.labels.filter(name=name).exists():
            raise ValidationError({"name": "That type already exists."})
        label = RelationLabel.objects.create(
            graph=graph,
            name=name,
            description=body.validated_data.get("description", ""),
            source=LabelSource.USER,
        )
        return Response(
            {
                "id": label.id,
                "name": label.name,
                "description": label.description,
                "source": label.source,
                "relation_count": 0,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"labels/(?P<label_id>\d+)",
    )
    def label_detail(self, request, pk=None, label_id=None):
        graph = self._graph(pk)
        label = get_object_or_404(RelationLabel, pk=label_id, graph=graph)
        if request.method == "DELETE":
            label.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        body = LabelWriteSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        name = body.validated_data.get("name")
        if name and graph.labels.filter(name=name).exclude(pk=label.pk).exists():
            raise ValidationError({"name": "That type already exists."})
        for field, value in body.validated_data.items():
            setattr(label, field, value)
        label.source = LabelSource.USER
        label.save()
        return Response(
            {
                "id": label.id,
                "name": label.name,
                "description": label.description,
                "source": label.source,
                "relation_count": label.relations.count(),
            }
        )

    @action(detail=True, methods=["get"], url_path=r"clusters/(?P<cluster_id>\d+)")
    def cluster_detail(self, request, pk=None, cluster_id=None):
        return Response(
            GraphViewService.cluster_detail(self._graph(pk), self._cluster(cluster_id))
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"clusters/(?P<cluster_id>\d+)/separate",
    )
    def separate(self, request, pk=None, cluster_id=None):
        graph = self._graph(pk)
        cluster = self._cluster(cluster_id)
        body = SeparateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        if not cluster.aliases.filter(key=body.validated_data["key"]).exists():
            raise ValidationError({"key": "That spelling is not in this entity."})
        if cluster.aliases.count() < 2:
            raise ValidationError({"key": "This entity has only one spelling."})
        EntityClusterService.separate(request.user, cluster, body.validated_data["key"])
        cluster.refresh_from_db()
        return Response(GraphViewService.cluster_detail(graph, cluster))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"clusters/(?P<cluster_id>\d+)/merge",
    )
    def merge(self, request, pk=None, cluster_id=None):
        graph = self._graph(pk)
        cluster = self._cluster(cluster_id)
        body = MergeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        other = self._cluster(body.validated_data["other_id"])
        if other.pk == cluster.pk:
            raise ValidationError({"other_id": "Pick a different entity."})
        EntityClusterService.merge(request.user, cluster, other)
        cluster.refresh_from_db()
        return Response(GraphViewService.cluster_detail(graph, cluster))
