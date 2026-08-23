"""Download and restore an account archive."""

import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from data_exports.constants import MAX_ARCHIVE_BYTES, ExportScope
from data_exports.services import export_service, restore_service

logger = logging.getLogger(__name__)


class AccountExportView(APIView):
    """Download the authenticated user's account as a zip archive."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get("scope", ExportScope.FULL)
        if scope not in ExportScope.values:
            return Response(
                {"detail": "scope must be one of: full, memories"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content = export_service.build_archive(request.user, scope)
        response = HttpResponse(content, content_type="application/zip")
        filename = export_service.archive_filename(request.user, scope)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = len(content)
        return response


class AccountRestoreView(APIView):
    """Rebuild the authenticated user's account from an uploaded archive."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("archive")
        if upload is None:
            return Response(
                {"detail": "Attach the .zip you exported as 'archive'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if upload.size > MAX_ARCHIVE_BYTES:
            return Response(
                {
                    "detail": (
                        f"That archive is {upload.size // (1024 * 1024)}MB, over "
                        f"the {MAX_ARCHIVE_BYTES // (1024 * 1024)}MB limit."
                    )
                },
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            report = restore_service.restore_archive(request.user, upload.read())
        except restore_service.RestoreError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(report.as_dict(), status=status.HTTP_201_CREATED)
