from django.urls import include, path

app_name = "feature_flags"

urlpatterns = [
    path("api/", include("feature_flags.api.urls")),
]
