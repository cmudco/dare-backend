from django.urls import path, include
from files.constants import APP_NAME
from .api.urls import router

app_name = APP_NAME

urlpatterns = [
    path("api/", include((router.urls, app_name), namespace="api"))
]