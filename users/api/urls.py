from django.urls import include, path
from rest_framework.routers import DefaultRouter

from users.api.views import UserStatsView, VectorDBViewSet
from users.constants import APP_NAME

app_name = APP_NAME

router = DefaultRouter()
router.register(r'vector-db', VectorDBViewSet, basename='vector-db')

urlpatterns = [
    path("dj-rest-auth/", include("dj_rest_auth.urls")),
    path("dj-rest-auth/registration/", include("dj_rest_auth.registration.urls")),
    path("stats/", UserStatsView.as_view(), name="user-stats"),

    path("", include(router.urls)),
]
