import os

import environ

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Initialising environment variables
env = environ.Env()
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
DEBUG = os.getenv("DJANGO_DEBUG")
DJANGO_SETTINGS_MODULE = os.getenv("DJANGO_SETTINGS_MODULE")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
SITE_ID = int(os.getenv("SITE_ID", 1))

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "https://dare-front.hss.cmu.edu").split(
    ","
)
CSRF_TRUSTED_ORIGINS = os.getenv("CSRF_TRUSTED_ORIGINS", "https://dare-front.hss.cmu.edu").split(",")

# database
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

EMAIL_HOST = os.getenv("EMAIL_HOST", None)
EMAIL_PORT = os.getenv("EMAIL_PORT", 587)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")

# sentry
SENTRY_DSN = os.getenv("SENTRY_DSN")

# frontend
FRONTEND_CONFIRM_EMAIL_URL = os.getenv("FRONTEND_CONFIRM_EMAIL_URL")
FRONTEND_PASSWORD_RESET_URL = os.getenv("FRONTEND_PASSWORD_RESET_URL")

PINECONE_API_KEY = env('PINECONE_API_KEY')
PINECONE_INDEX_NAME = env('PINECONE_INDEX_NAME')
OPENAI_API_KEY = env('OPENAI_API_KEY')
CLAUDE_API_KEY = env('CLAUDE_API_KEY')

# redis
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_DB = os.getenv("REDIS_DB")
