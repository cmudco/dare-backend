from django.urls import include, path

from research.constants import APP_NAME

app_name = APP_NAME

urlpatterns = [
    path("api/research/", include("research.api.urls")),
]
