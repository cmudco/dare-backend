from django.urls import include, path

from users.constants import APP_NAME

app_name = APP_NAME

urlpatterns = [
    path("dj-rest-auth/", include("dj_rest_auth.urls")),
    path("dj-rest-auth/registration/", include("dj_rest_auth.registration.urls")),
]
