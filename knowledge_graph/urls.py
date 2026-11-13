from django.urls import include, path

from knowledge_graph.constants import APP_NAME

app_name = APP_NAME

urlpatterns = [
    path("api/", include("knowledge_graph.api.urls")),
]
