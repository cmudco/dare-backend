"""HTTP endpoints for the memory feature."""

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from memory.services import api_service as memory_service
from memory.services import backfill, portability

from .serializers import (
    ClearResponseSerializer,
    MemoryBackfillRequestSerializer,
    MemoryBackfillResponseSerializer,
    MemoryBackfillRunSerializer,
    MemoryItemSerializer,
    MemorySearchRequestSerializer,
    MemorySearchResponseSerializer,
)

SERVICE_ERROR_STATUSES = {
    memory_service.MemoryNotFound: status.HTTP_404_NOT_FOUND,
    memory_service.MemoryInvalid: status.HTTP_400_BAD_REQUEST,
    memory_service.MemoryConflict: status.HTTP_409_CONFLICT,
    memory_service.MemoryUnavailable: status.HTTP_502_BAD_GATEWAY,
}


def _service_error_response(error):
    response_status = SERVICE_ERROR_STATUSES.get(
        type(error), status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    if not str(error) and not error.payload:
        return Response(status=response_status)
    return Response(
        {"detail": str(error), **error.payload},
        status=response_status,
    )


class MemoryViewSet(viewsets.ViewSet):
    """Expose the authenticated user's memory store."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        items = memory_service.list_items(
            request.user,
            retired=request.query_params.get("state") == "retired",
        )
        return Response(MemoryItemSerializer(items, many=True).data)

    def retrieve(self, request, pk=None):
        try:
            item = memory_service.get_item(request.user, pk)
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(MemoryItemSerializer(item).data)

    def partial_update(self, request, pk=None):
        content = request.data.get("content")
        if content is None:
            return Response(
                {"detail": "A content field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            item = memory_service.update_item(request.user, pk, str(content))
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        if item is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(MemoryItemSerializer(item).data)

    def destroy(self, request, pk=None):
        try:
            memory_service.forget_item(request.user, pk)
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"])
    def search(self, request):
        serializer = MemorySearchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = memory_service.search_items(
            request.user,
            serializer.validated_data["query"].strip(),
        )
        return Response(MemorySearchResponseSerializer(payload).data)

    @action(detail=False, methods=["delete"])
    def clear(self, request):
        try:
            payload = memory_service.clear_store(request.user)
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(ClearResponseSerializer(payload).data)

    def document(self, request):
        try:
            if request.method.lower() == "get":
                payload = memory_service.get_document(request.user)
            else:
                payload = memory_service.save_document(
                    request.user, request.data.get("markdown")
                )
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(payload)

    def consolidate(self, request):
        try:
            if request.method.lower() == "get":
                payload = memory_service.get_consolidation(request.user)
            else:
                payload = memory_service.apply_consolidation(
                    request.user, request.data or {}
                )
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(payload)

    def ledger(self, request):
        return Response(
            memory_service.get_ledger(
                request.user,
                request.query_params.get("limit", 100),
            )
        )

    def hold(self, request):
        try:
            item = memory_service.set_hold(
                request.user,
                request.data.get("id"),
                request.data.get("held"),
            )
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(MemoryItemSerializer(item).data)

    def sessions(self, request):
        query = (request.query_params.get("q") or "").strip()[:2000]
        since = (request.query_params.get("since") or "").strip() or None
        until = (request.query_params.get("until") or "").strip() or None
        try:
            payload = memory_service.get_sessions(
                request.user,
                query=query,
                since=since,
                until=until,
            )
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(payload)

    def export(self, request):
        return Response(portability.export_bundle(request.user))

    def import_bundle(self, request):
        try:
            payload = portability.import_bundle(request.user, request.data)
        except portability.ImportError_ as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=status.HTTP_201_CREATED)

    def import_foreign(self, request):
        try:
            payload = portability.import_foreign(request.user, request.data.get("text"))
        except portability.ImportError_ as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=status.HTTP_202_ACCEPTED)

    def recall(self, request):
        query = (request.query_params.get("q") or "").strip()[:2000]
        try:
            payload = memory_service.get_recall(request.user, query)
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(payload)

    @extend_schema(
        methods=["GET"],
        summary="Get historical memory build progress",
        responses={200: MemoryBackfillResponseSerializer},
    )
    @extend_schema(
        methods=["POST"],
        summary="Build memory from historical conversations",
        request=MemoryBackfillRequestSerializer,
        responses={
            200: MemoryBackfillResponseSerializer,
            202: MemoryBackfillResponseSerializer,
        },
    )
    @extend_schema(
        methods=["DELETE"],
        summary="Stop an active historical memory build",
        responses={200: MemoryBackfillResponseSerializer},
    )
    def backfill(self, request):
        if request.method.lower() == "get":
            run = backfill.latest_run(request.user)
            return Response(
                {
                    "run": (
                        MemoryBackfillRunSerializer(run).data
                        if run is not None
                        else None
                    )
                }
            )

        if request.method.lower() == "delete":
            run = backfill.stop_run(request.user)
            return Response(
                {
                    "run": (
                        MemoryBackfillRunSerializer(run).data
                        if run is not None
                        else None
                    )
                }
            )

        input_serializer = MemoryBackfillRequestSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            run, created = backfill.start_run(
                request.user,
                since=input_serializer.validated_data.get("since"),
                until=input_serializer.validated_data.get("until"),
            )
        except memory_service.MemoryServiceError as error:
            return _service_error_response(error)
        return Response(
            {"run": MemoryBackfillRunSerializer(run).data},
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )
