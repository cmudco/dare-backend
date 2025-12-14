from django.urls import include, path
from rest_framework.routers import DefaultRouter
from users.api.views import (
    UserStatsView,
    VectorDBViewSet,
    ChunkingSettingsViewSet,
    AccessCodeCheckView,
    AvatarViewSet,
    token_health_check,
)
from users.constants import APP_NAME

app_name = APP_NAME

router = DefaultRouter()
router.register(r'vector-db', VectorDBViewSet, basename='vector-db')
router.register(r'chunking', ChunkingSettingsViewSet, basename='chunking')
router.register(r'avatar', AvatarViewSet, basename='avatar')

urlpatterns = [
    path("dj-rest-auth/", include("dj_rest_auth.urls")),
    path("dj-rest-auth/registration/", include("dj_rest_auth.registration.urls")),
    path("stats/", UserStatsView.as_view(), name="user-stats"),
    path("token-health/", token_health_check, name="token-health-check"),
    path("access-codes/check/", AccessCodeCheckView.as_view(), name="access-code-check"),

    path("", include(router.urls)),
]


