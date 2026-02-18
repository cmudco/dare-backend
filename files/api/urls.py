from django.urls import path, include
from rest_framework.routers import DefaultRouter

from files.api.views import (
    FileViewSet,
    TagViewSet,
    FolderViewSet,
    FileViewAPIView,
    InternalFileUploadView,
)
from files.constants import APP_NAME

router = DefaultRouter()
router.register(r'files', FileViewSet, basename='file')
router.register(r'tags', TagViewSet, basename='tag')
router.register(r'folders', FolderViewSet, basename='folder')

app_name = APP_NAME

urlpatterns = [
    path('', include(router.urls)),
    path('files/<int:file_id>/view/', FileViewAPIView.as_view(), name='file-view'),
    path('internal/upload/', InternalFileUploadView.as_view(), name='internal-file-upload'),
]