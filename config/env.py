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

EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=False)
EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL', default=False)

# sentry
SENTRY_DSN = os.getenv("SENTRY_DSN")

# frontend
FRONTEND_CONFIRM_EMAIL_URL = os.getenv("FRONTEND_CONFIRM_EMAIL_URL")
FRONTEND_PASSWORD_RESET_URL = os.getenv("FRONTEND_PASSWORD_RESET_URL")

# Platform URLs for unified authentication
DARE_FRONTEND_URL = os.getenv("DARE_FRONTEND_URL")
SOCRATIC_BOTS_FRONTEND_URL = os.getenv("SOCRATIC_BOTS_FRONTEND_URL")
DARE_BACKEND_URL = os.getenv("DARE_BACKEND_URL")
SOCRATIC_BOTS_BACKEND_URL = os.getenv("SOCRATIC_BOTS_BACKEND_URL")

PINECONE_API_KEY = env('PINECONE_API_KEY')
PINECONE_INDEX_NAME = env('PINECONE_INDEX_NAME')
OPENAI_API_KEY = env('OPENAI_API_KEY')
CLAUDE_API_KEY = env('CLAUDE_API_KEY')
GEMINI_API_KEY = env('GEMINI_API_KEY')
OLLAMA_HOST = env('OLLAMA_HOST', default='http://localhost:11434')
ELEVENLABS_API_KEY = env('ELEVENLABS_API_KEY', default='sk_abd91c9207d2e10ee703cf032277a11121acf407210b9749')

# redis
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_DB = os.getenv("REDIS_DB")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

# Add these configurations
WEAVIATE_HOST = os.getenv("WEAVIATE_HOST", "localhost")
WEAVIATE_PORT = int(os.getenv("WEAVIATE_PORT", "8080"))
WEAVIATE_COLLECTION_NAME = os.getenv("WEAVIATE_COLLECTION_NAME", "Document")
WEAVIATE_SKIP_INIT_CHECKS = os.getenv("WEAVIATE_SKIP_INIT_CHECKS", "True") == "True"
WEAVIATE_AUTOSCHEMA_ENABLED = os.getenv("WEAVIATE_AUTOSCHEMA_ENABLED", "False") == "True"

# MCP Docker Configuration
MCP_USE_DOCKER = os.getenv("MCP_USE_DOCKER", "False") == "True"

# Database toggle for local development
# Set to True to use PostgreSQL (same as staging/prod), False for SQLite
USE_POSTGRES = os.getenv("USE_POSTGRES", "False").lower() in ("true", "1", "yes")
# Internal API key for inter-service communication (SB backend -> DARE backend)
DARE_INTERNAL_KEY = os.getenv("DARE_INTERNAL_KEY", "local-dev-internal-key")


# SyftBox Configuration
SYFTBOX_ENABLED = os.getenv("SYFTBOX_ENABLED", "False") == "True"
SYFTBOX_DATASITES_ROOT = os.getenv("SYFTBOX_DATASITES_ROOT", None)
SYFTBOX_APP_NAME = os.getenv("SYFTBOX_APP_NAME", "dare")
SYFTBOX_BASE_URL = os.getenv("SYFTBOX_BASE_URL", "https://syftbox.net")
SYFTBOX_SYNC_INTERVAL_SECONDS = int(os.getenv("SYFTBOX_SYNC_INTERVAL_SECONDS", "300"))
