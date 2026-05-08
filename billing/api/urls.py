from django.urls import include, path
from rest_framework.routers import DefaultRouter

from billing.api.views import (
    BillingViewSet,
    GroupWalletViewSet,
    LiteLLMKeyViewSet,
    SystemRefillPolicyViewSet,
)
from billing.constants import APP_NAME

router = DefaultRouter()
router.register(r'billing', BillingViewSet, basename='billing')
router.register(r'billing/group-wallets', GroupWalletViewSet, basename='group-wallets')
router.register(r'billing/refill-policy', SystemRefillPolicyViewSet, basename='refill-policy')
router.register(r'billing/wallets/litellm', LiteLLMKeyViewSet, basename='litellm-keys')

app_name = APP_NAME

urlpatterns = [
    path("", include(router.urls)),
]
