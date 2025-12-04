from django.urls import path, include
from conversations.constants import APP_NAME
from .api.urls import urlpatterns as api_urlpatterns

app_name = APP_NAME

urlpatterns = [
    path("api/", include((api_urlpatterns, app_name), namespace="api"))
]