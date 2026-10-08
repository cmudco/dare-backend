from django.urls import include, path

from assistant.constants import APP_NAME

app_name = APP_NAME

urlpatterns = [
    path("api/", include("assistant.api.urls")),
]
