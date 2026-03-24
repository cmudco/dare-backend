from django.urls import path, include

app_name = "sharing"

urlpatterns = [
    path("api/", include("sharing.api.urls")),
]
