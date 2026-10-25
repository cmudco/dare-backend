from django.urls import path

from core.api.views import BackgroundModelsView, PlatformBackgroundModelsView

app_name = "core"

urlpatterns = [
    path(
        "api/background-models/",
        BackgroundModelsView.as_view(),
        name="background-models",
    ),
    path(
        "api/platform/background-models/",
        PlatformBackgroundModelsView.as_view(),
        name="platform-background-models",
    ),
]
