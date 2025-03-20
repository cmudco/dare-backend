from config.env import BASE_DIR
from config.settings.common import *

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(env.BASE_DIR, "db.sqlite3"),
    }
}

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.1/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(env.BASE_DIR, "static")
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(env.BASE_DIR, "media")

COMPRESS_URL = STATIC_URL
COMPRESS_ROOT = STATIC_ROOT

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True
