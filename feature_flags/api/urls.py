from django.urls import path

from feature_flags.api.views import MyFeatureFlagsView

app_name = "feature_flags_api"

urlpatterns = [
    path("feature-flags/me/", MyFeatureFlagsView.as_view(), name="my-feature-flags"),
]
