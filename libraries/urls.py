from django.urls import include, path

app_name = "libraries"

urlpatterns = [
    path("api/", include("libraries.api.urls")),
]
