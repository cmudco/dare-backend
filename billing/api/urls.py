from django.urls import path, include
from rest_framework.routers import DefaultRouter
from billing.api.views import BillingViewSet
from billing.constants import APP_NAME

router = DefaultRouter()
router.register(r'billing', BillingViewSet, basename='billing')

app_name = APP_NAME

urlpatterns = [
    path("", include(router.urls)),
]