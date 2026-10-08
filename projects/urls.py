from django.urls import include, path

from projects.constants import APP_NAME

app_name = APP_NAME

urlpatterns = [
    path("api/", include("projects.api.urls")),
]
