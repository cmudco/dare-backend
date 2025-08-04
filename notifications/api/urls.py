from django.urls import path, include
from rest_framework.routers import DefaultRouter
from notifications.api.views import NotificationViewSet

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')

app_name = 'notifications_api'

urlpatterns = [
    path('', include(router.urls)),
]