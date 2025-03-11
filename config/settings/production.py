from corsheaders.defaults import default_headers

from config.settings.common import *

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.DB_NAME,
        "USER": env.DB_USER,
        "PASSWORD": env.DB_PASSWORD,
        "HOST": env.DB_HOST,
        "PORT": env.DB_PORT,
    }
}

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(env.BASE_DIR, "static")

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(env.BASE_DIR, "media")

COMPRESS_URL = STATIC_URL
COMPRESS_ROOT = STATIC_ROOT

# CORS configuration
CORS_ALLOWED_ORIGINS = (
    env.CORS_ALLOWED_ORIGINS if hasattr(env, "CORS_ALLOWED_ORIGINS") else []
)
CORS_ALLOW_CREDENTIALS = True 

CORS_ALLOW_HEADERS = list(default_headers) + [
    "authorization",
    "content-type",
    "x-csrftoken",
]

CORS_ALLOW_METHODS = [
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
]

CSRF_TRUSTED_ORIGINS = env.CSRF_TRUSTED_ORIGINS if hasattr(env, "CSRF_TRUSTED_ORIGINS") else []
