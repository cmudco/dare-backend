from django.urls import include, path

app_name = "memory"

urlpatterns = [
    path("api/memory/", include("memory.api.urls")),
]
