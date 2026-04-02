from django.urls import include, path
from rest_framework.routers import DefaultRouter

from syftbox.api.views import SyftboxAuthView

router = DefaultRouter()
router.register(r"auth", SyftboxAuthView, basename="syftbox-auth")

urlpatterns = [
    path("", include(router.urls)),
]
