from django.urls import include, path

from release_notes.constants import APP_NAME

app_name = APP_NAME

urlpatterns = [
    path("api/", include("release_notes.api.urls")),
]
