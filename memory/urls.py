from django.urls import path, include

app_name = "memory"

urlpatterns = [
    path("api/memory/", include("memory.api.urls")),
]
