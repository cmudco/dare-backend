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
# Tags every event so dev and production are separable in the Sentry UI.
# Without it the SDK reports everything as "production".
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "development")
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

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
ELEVENLABS_API_KEY = env('ELEVENLABS_API_KEY', default='')

# redis
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = os.getenv("REDIS_PORT")
REDIS_DB = os.getenv("REDIS_DB")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

# Add these configurations
WEAVIATE_HOST = os.getenv("WEAVIATE_HOST", "localhost")
WEAVIATE_PORT = int(os.getenv("WEAVIATE_PORT", "8080"))
# Searches travel over gRPC, not the HTTP port above. Deployments that remap
# the HTTP port to dodge a collision almost always remap gRPC too, and the
# client's 50051 default will then silently point at whatever else is there.
WEAVIATE_GRPC_PORT = int(os.getenv("WEAVIATE_GRPC_PORT", "50051"))
WEAVIATE_COLLECTION_NAME = os.getenv("WEAVIATE_COLLECTION_NAME", "Document")
WEAVIATE_SKIP_INIT_CHECKS = os.getenv("WEAVIATE_SKIP_INIT_CHECKS", "True") == "True"
WEAVIATE_AUTOSCHEMA_ENABLED = os.getenv("WEAVIATE_AUTOSCHEMA_ENABLED", "False") == "True"

# MCP Docker Configuration
MCP_USE_DOCKER = os.getenv("MCP_USE_DOCKER", "False") == "True"

# Hermes agent runtime (delegated research-agent runtime for Research Mode)
HERMES_GATEWAY_URL = os.getenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "dev-spike-local")
# DARE writes its canonical soul into the gateway profile's SOUL.md (the anchor).
HERMES_SYNC_SOUL = os.getenv("HERMES_SYNC_SOUL", "True") == "True"
HERMES_SOUL_PATH = os.getenv("HERMES_SOUL_PATH", os.path.expanduser("~/.hermes/SOUL.md"))
# One project = one Hermes profile. A profile is its own HERMES_HOME directory,
# so its SOUL.md and memories/{MEMORY,USER}.md are isolated by construction
# rather than by convention. The gateway serves every profile from the single
# multiplex listener under /p/<profile>/, so this costs no extra process.
# Off => every project shares the one default profile (the pre-multiplex path).
HERMES_PROFILE_PER_PROJECT = os.getenv("HERMES_PROFILE_PER_PROJECT", "True") == "True"
HERMES_PROFILES_ROOT = os.getenv(
    "HERMES_PROFILES_ROOT", os.path.expanduser("~/.hermes/profiles")
)
# Profile names must match Hermes's [a-z0-9][a-z0-9_-]{0,63}.
HERMES_PROFILE_PREFIX = os.getenv("HERMES_PROFILE_PREFIX", "dare-proj")
# Model pinned into every project profile. A profile with no model of its own
# inherits the runtime's current default, which means an operator running
# `hermes model` silently re-points every research project — and a model that
# refuses (content_filter) takes chat down with it. Pin it so the project's
# agent is a DARE decision, not a side effect of gateway config.
HERMES_PROFILE_MODEL = os.getenv("HERMES_PROFILE_MODEL", "claude-sonnet-5")
HERMES_PROFILE_MODEL_PROVIDER = os.getenv("HERMES_PROFILE_MODEL_PROVIDER", "anthropic")
# Lifetime of the DARE-minted JWT written into a profile's .env so its MCP tools
# (web search / page fetch) run as the project's owner, not a shared identity.
HERMES_PROFILE_TOKEN_DAYS = int(os.getenv("HERMES_PROFILE_TOKEN_DAYS", "365"))

# How long a run's SSE stream may go without a real event before DARE stops
# reading and resolves the outcome from Hermes's pollable run record instead.
# Generous: a deep run can think for a while between tool calls. See
# HermesService.stream_events for why a plain socket timeout is not enough.
HERMES_STREAM_IDLE_SECONDS = int(os.getenv("HERMES_STREAM_IDLE_SECONDS", "180"))
# Where a profile's MCP client reaches back to DARE. This is written into each
# profile's config.yaml, so it must be resolvable from the machine running the
# gateway, not from the browser.
DARE_MCP_GATEWAY_URL = os.getenv(
    "DARE_MCP_GATEWAY_URL",
    (
        (DARE_BACKEND_URL.rstrip("/") + "/mcp/api/gateway/")
        if DARE_BACKEND_URL
        else "http://127.0.0.1:8000/mcp/api/gateway/"
    ),
)

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
