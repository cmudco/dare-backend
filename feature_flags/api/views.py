from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from feature_flags.services import resolve_flags_for_user


class MyFeatureFlagsView(APIView):
    """
    Return the resolved feature flag map for the authenticated user.

    Resolution precedence: user override > access-code-group override > flag default.
    Response keys are snake_case from the DB and converted to camelCase by the
    DRF camelCase renderer (e.g. ``enable_byok`` -> ``enableByok``).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        flags = resolve_flags_for_user(request.user)
        return Response({"flags": flags})
