"""
URL configuration for API Keys app
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from api_keys.views import UserProviderAPIKeyViewSet

router = DefaultRouter()
router.register(r'user-api-keys', UserProviderAPIKeyViewSet, basename='user-api-keys')

urlpatterns = [
    path('api/', include(router.urls)),
]
