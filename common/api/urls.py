from django.urls import path

from common.api.views import health_check, readiness_check
from common.constants import APP_NAME

app_name = APP_NAME

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("ready/", readiness_check, name="readiness-check"),
]
