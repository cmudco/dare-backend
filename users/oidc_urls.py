from django.urls import path

from users.api.oidc_views import oidc_callback, oidc_login

# allauth reverses these names to build the redirect URI it sends the provider.
urlpatterns = [
    path("<str:provider_id>/login/", oidc_login, name="openid_connect_login"),
    path(
        "<str:provider_id>/login/callback/",
        oidc_callback,
        name="openid_connect_callback",
    ),
]
