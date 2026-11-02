from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsSuperAdmin
from core.api.serializers import (
    BackgroundModelOverviewSerializer,
    BackgroundModelSelectionSerializer,
    PlatformBackgroundModelsSerializer,
    PlatformBackgroundModelsUpdateSerializer,
)
from core.constants import BackgroundTask
from core.services.background_model_service import (
    BackgroundModelNotOffered,
    background_model_overview,
    platform_background_models,
    set_user_background_model,
    update_platform_background_models,
)

_NOT_OFFERED = _("That model cannot run background tasks for this account.")


class BackgroundModelsView(APIView):
    """The models behind this user's background tasks, and their own choice."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return self._overview(request)

    def patch(self, request):
        payload = BackgroundModelSelectionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            set_user_background_model(request.user, payload.validated_data["model"])
        except BackgroundModelNotOffered:
            return Response(
                {"model": [_NOT_OFFERED]}, status=status.HTTP_400_BAD_REQUEST
            )
        return self._overview(request)

    @staticmethod
    def _overview(request):
        overview = background_model_overview(request.user)
        return Response(
            BackgroundModelOverviewSerializer(
                {
                    **overview.__dict__,
                    "can_manage_platform": IsSuperAdmin().has_permission(request, None),
                }
            ).data
        )


class PlatformBackgroundModelsView(APIView):
    """Platform default and per-task overrides; the same row as the Django admin."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]

    def get(self, request):
        return Response(
            PlatformBackgroundModelsSerializer(platform_background_models()).data
        )

    def patch(self, request):
        payload = PlatformBackgroundModelsUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            update_platform_background_models(
                default=data.get("default"),
                overrides={
                    BackgroundTask(item["task"]): item["model"]
                    for item in data.get("overrides", [])
                },
            )
        except BackgroundModelNotOffered:
            return Response(
                {"model": [_NOT_OFFERED]}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            PlatformBackgroundModelsSerializer(platform_background_models()).data
        )
