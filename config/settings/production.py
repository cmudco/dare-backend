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

CORS_ALLOWED_ORIGINS = (
    env.CORS_ALLOWED_ORIGINS if hasattr(env, "CORS_ALLOWED_ORIGINS") else []
)
CORS_ALLOW_CREDENTIALS = True
