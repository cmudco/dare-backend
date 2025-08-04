from django.urls import path, include
from files.constants import APP_NAME
from . import api

app_name = APP_NAME

urlpatterns = [
    path("api/", include("files.api.urls"))
]