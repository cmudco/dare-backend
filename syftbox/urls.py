from django.urls import include, path

app_name = "syftbox"

urlpatterns = [
    path("api/", include("syftbox.api.urls")),
]
