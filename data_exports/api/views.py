import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from data_exports.services.constants import DataExportScope
from data_exports.services.dtos import DataExportRequest
from data_exports.services.export_service import DataExportService

logger = logging.getLogger(__name__)


class DataExportDownloadView(APIView):
    """Download a ZIP archive of the authenticated user's portable DARE context."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope_value = request.query_params.get("scope", DataExportScope.FULL.value)

        try:
            export_scope = DataExportScope.from_value(scope_value)
        except ValueError:
            return Response(
                {"error": "scope must be one of: full, memories"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        export_request = DataExportRequest(
            user=request.user,
            scope=export_scope,
            generated_at=timezone.now(),
        )

        try:
            result = DataExportService().generate_export(export_request)
        except Exception as exc:
            logger.exception("Failed to generate DARE data export: %s", exc)
            return Response(
                {"error": "Failed to generate data export"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response = HttpResponse(result.content, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{result.filename}"'
        response["Content-Length"] = len(result.content)
        return response
