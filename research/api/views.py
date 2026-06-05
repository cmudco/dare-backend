from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# isort: off
from research.api.serializers import (
    ProjectSoulFileSelectionSerializer,
    ResearchAgentRunCreateSerializer,
    ResearchAgentRunSerializer,
    ResearchHermesRunCallbackSerializer,
    ResearchKnowledgeItemSerializer,
    ResearchProjectSerializer,
    ResearchSoulFileSerializer,
    ResearchSoulFileVersionSerializer,
    ResearchSourceSerializer,
    ResearchStagingItemSerializer,
    ReviewReasonSerializer,
)
from research.constants import RESEARCH_METADATA
from research.models import (
    ResearchAgentRun,
    ResearchKnowledgeItem,
    ResearchProject,
    ResearchProjectStatus,
    ResearchSoulFile,
    ResearchSoulFileVersion,
    ResearchSource,
    ResearchStagingItem,
)
from research.permissions import CanAccessResearch
from research.services import (
    ResearchAgentRunService,
    ResearchReviewService,
    ResearchSoulFileService,
)

# isort: on


class ResearchMetadataView(APIView):
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get(self, request):
        return Response(RESEARCH_METADATA)


class ResearchProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ResearchProjectSerializer
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get_queryset(self):
        return (
            ResearchProject.active_objects.filter(user=self.request.user)
            .select_related("active_soul_file")
            .prefetch_related("sources", "staging_items", "knowledge_items")
            .order_by("-updated_at")
        )

    def perform_create(self, serializer):
        project = serializer.save(user=self.request.user)
        ResearchSoulFileService.ensure_project_soul_file(project, self.request.user)

    def perform_destroy(self, instance):
        instance.soft_delete()

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        project = self.get_object()
        project.status = ResearchProjectStatus.ARCHIVED
        project.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        project = self.get_object()
        project.status = ResearchProjectStatus.ACTIVE
        project.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=["post"], url_path="select-soul-file")
    def select_soul_file(self, request, pk=None):
        project = self.get_object()
        serializer = ProjectSoulFileSelectionSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        project.active_soul_file = serializer.validated_data["soul_file"]
        project.save(update_fields=["active_soul_file", "updated_at"])
        return Response(self.get_serializer(project).data)


class ResearchSoulFileViewSet(viewsets.ModelViewSet):
    serializer_class = ResearchSoulFileSerializer
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get_queryset(self):
        return (
            ResearchSoulFile.active_objects.filter(user=self.request.user)
            .prefetch_related("versions")
            .order_by("-is_default", "-updated_at")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        soul_file = ResearchSoulFileService.create_soul_file(
            user=request.user,
            title=serializer.validated_data.get("title", ""),
            description=serializer.validated_data.get("description", ""),
            template_key=serializer.validated_data.get("template_key", "custom"),
            body=serializer.validated_data.get("body", ""),
            is_default=serializer.validated_data.get("is_default", False),
        )
        return Response(
            self.get_serializer(soul_file).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        current_version = instance.current_version
        soul_file = ResearchSoulFileService.update_soul_file(
            soul_file=instance,
            user=request.user,
            title=serializer.validated_data.get("title", instance.title),
            description=serializer.validated_data.get(
                "description",
                instance.description,
            ),
            template_key=serializer.validated_data.get(
                "template_key",
                instance.template_key,
            ),
            body=serializer.validated_data.get(
                "body",
                current_version.body if current_version is not None else "",
            ),
            is_default=serializer.validated_data.get("is_default", instance.is_default),
        )
        return Response(self.get_serializer(soul_file).data)

    def perform_destroy(self, instance):
        instance.soft_delete()


class ResearchSoulFileVersionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResearchSoulFileVersionSerializer
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get_queryset(self):
        queryset = (
            ResearchSoulFileVersion.active_objects.filter(
                user=self.request.user,
                soul_file__user=self.request.user,
            )
            .select_related("soul_file", "created_by")
            .order_by("-version_number")
        )
        soul_file_id = self.request.query_params.get(
            "soulFile"
        ) or self.request.query_params.get("soul_file")
        if soul_file_id:
            queryset = queryset.filter(soul_file_id=soul_file_id)
        return queryset


class ResearchSourceViewSet(viewsets.ModelViewSet):
    serializer_class = ResearchSourceSerializer
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get_queryset(self):
        queryset = ResearchSource.active_objects.filter(
            project__user=self.request.user,
            user=self.request.user,
        ).select_related("project")
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset.order_by("-created_at")

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        serializer.save(user=self.request.user, project=project)

    def perform_destroy(self, instance):
        instance.soft_delete()


class ResearchStagingItemViewSet(viewsets.ModelViewSet):
    serializer_class = ResearchStagingItemSerializer
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get_queryset(self):
        queryset = (
            ResearchStagingItem.active_objects.filter(
                project__user=self.request.user,
                user=self.request.user,
            )
            .select_related("project", "source", "reviewed_by", "soul_file_version")
            .order_by("-created_at")
        )
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        review_status = self.request.query_params.get("status")
        if review_status:
            queryset = queryset.filter(status=review_status)
        return queryset

    def perform_create(self, serializer):
        project = serializer.validated_data["project"]
        soul_file_version = ResearchSoulFileService.get_project_active_version(
            project,
            self.request.user,
        )
        serializer.save(
            user=self.request.user,
            project=project,
            soul_file_version=soul_file_version,
        )

    def perform_destroy(self, instance):
        instance.soft_delete()

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        staging_item = self.get_object()
        knowledge_item = ResearchReviewService.approve(staging_item, request.user)
        return Response(
            {
                "staging_item": self.get_serializer(staging_item).data,
                "knowledge_item": ResearchKnowledgeItemSerializer(
                    knowledge_item,
                    context=self.get_serializer_context(),
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = ReviewReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staging_item = ResearchReviewService.reject(
            self.get_object(),
            request.user,
            serializer.validated_data["reason"],
        )
        return Response(self.get_serializer(staging_item).data)

    @action(detail=True, methods=["post"], url_path="later")
    def mark_later(self, request, pk=None):
        serializer = ReviewReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staging_item = ResearchReviewService.mark_later(
            self.get_object(),
            request.user,
            serializer.validated_data["reason"],
        )
        return Response(self.get_serializer(staging_item).data)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        staging_item = ResearchReviewService.restore(self.get_object())
        return Response(self.get_serializer(staging_item).data)


class ResearchAgentRunViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get_serializer_class(self):
        if self.action == "create":
            return ResearchAgentRunCreateSerializer
        return ResearchAgentRunSerializer

    def get_queryset(self):
        queryset = (
            ResearchAgentRun.active_objects.filter(
                project__user=self.request.user,
                user=self.request.user,
            )
            .select_related("project", "soul_file_version")
            .prefetch_related("tool_calls")
            .order_by("-created_at")
        )
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        role = self.request.query_params.get("role")
        if role:
            queryset = queryset.filter(role=role)
        run_status = self.request.query_params.get("status")
        if run_status:
            queryset = queryset.filter(status=run_status)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = ResearchAgentRunService.create_run(
            user=request.user,
            project=serializer.validated_data["project"],
            role=serializer.validated_data["role"],
            task=serializer.validated_data["task"],
            selected_context=serializer.validated_data.get("selected_context"),
            allowed_tools=serializer.validated_data.get("allowed_tools", []),
            output_destination=serializer.validated_data.get("output_destination"),
        )
        return Response(
            ResearchAgentRunSerializer(
                run,
                context=self.get_serializer_context(),
            ).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_destroy(self, instance):
        instance.soft_delete()

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        run = ResearchAgentRunService.cancel_run(self.get_object())
        return Response(
            ResearchAgentRunSerializer(
                run,
                context=self.get_serializer_context(),
            ).data
        )


class ResearchKnowledgeItemViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResearchKnowledgeItemSerializer
    permission_classes = [IsAuthenticated, CanAccessResearch]

    def get_queryset(self):
        queryset = (
            ResearchKnowledgeItem.active_objects.filter(
                project__user=self.request.user,
                user=self.request.user,
            )
            .select_related("project", "source", "staging_item", "approved_by")
            .select_related("soul_file_version")
            .order_by("-approved_at")
        )
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset


class ResearchHermesRunCallbackView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, run_id):
        internal_key = request.headers.get("X-Internal-Key", "")
        expected_key = getattr(
            settings,
            "HERMES_INTERNAL_KEY",
            getattr(settings, "DARE_INTERNAL_KEY", ""),
        )
        if not internal_key or internal_key != expected_key:
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        try:
            run = ResearchAgentRun.active_objects.select_related(
                "project",
                "soul_file_version",
            ).get(run_id=run_id)
        except ResearchAgentRun.DoesNotExist:
            return Response(
                {"error": "Research agent run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ResearchHermesRunCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run = ResearchAgentRunService.apply_callback(
            run=run,
            status=serializer.validated_data["status"],
            status_message=serializer.validated_data["status_message"],
            error_message=serializer.validated_data["error_message"],
            external_run_id=serializer.validated_data["external_run_id"],
            cost_usd=serializer.validated_data["cost_usd"],
            tool_calls=serializer.validated_data["tool_calls"],
        )
        return Response(ResearchAgentRunSerializer(run).data)


__all__ = [
    "ResearchAgentRunViewSet",
    "ResearchHermesRunCallbackView",
    "ResearchKnowledgeItemViewSet",
    "ResearchMetadataView",
    "ResearchProjectViewSet",
    "ResearchSoulFileVersionViewSet",
    "ResearchSoulFileViewSet",
    "ResearchSourceViewSet",
    "ResearchStagingItemViewSet",
]
