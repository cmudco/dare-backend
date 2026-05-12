from django.conf import settings
from django.db import connection
from django.utils import timezone
from redis import Redis
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    return Response(
        {
            "status": "ok",
            "service": "dare-backend",
            "environment": getattr(settings, "ENVIRONMENT", "unknown"),
            "timestamp": timezone.now().isoformat(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def readiness_check(request):
    checks = {}

    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"

    try:
        Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        ).ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc.__class__.__name__}"

    is_ready = all(value == "ok" for value in checks.values())
    response_status = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return Response(
        {
            "status": "ready" if is_ready else "not_ready",
            "service": "dare-backend",
            "checks": checks,
            "timestamp": timezone.now().isoformat(),
        },
        status=response_status,
    )
