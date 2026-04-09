from pathlib import Path

from config.env import BASE_DIR, env, USE_POSTGRES
from config.settings.common import *

# Database - Toggle between SQLite and PostgreSQL via USE_POSTGRES env var
if USE_POSTGRES:
    # Use PostgreSQL (same as staging/prod)
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
else:
    # Use SQLite for local development
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

WEAVIATE = {
    'HOST': 'localhost',
    'PORT': 8080,
    'COLLECTION_NAME': 'Document',
    'SKIP_INIT_CHECKS': True
}

# SyftBox local development settings
SYFTBOX = {
    'ENABLED': env.SYFTBOX_ENABLED,
    'DATASITES_ROOT': env.SYFTBOX_DATASITES_ROOT or os.path.join(
        str(Path.home()), 'SyftBox', 'datasites'
    ),
    'APP_NAME': env.SYFTBOX_APP_NAME,
}
