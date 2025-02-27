from django.urls import include, path

urlpatterns = [
    path("api/", include("common.api.urls", namespace="common-api")),
]
