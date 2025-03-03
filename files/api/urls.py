from django.urls import path, include
from rest_framework.routers import DefaultRouter
from files.api.views import FileViewSet
from files.constants import APP_NAME

router = DefaultRouter()
router.register(r'', FileViewSet, basename='file')


app_name = APP_NAME

urlpatterns = [
]