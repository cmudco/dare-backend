from django.urls import include, path

from users.api.urls import urlpatterns as api_urlpatterns

urlpatterns = [
    path("api/", include((api_urlpatterns, "users"), namespace="api")),
]
