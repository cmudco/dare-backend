"""
Socket.IO Server Configuration

This module creates and configures the Socket.IO async server
with Redis manager for horizontal scaling support.

The server is ASGI-compatible and wraps the existing Django app.
"""

import socketio
from django.conf import settings

# Build Redis URL using the same config as Django Channels
# This ensures Socket.IO uses the same Redis instance
REDIS_HOST = getattr(settings, 'REDIS_HOST', '127.0.0.1')
REDIS_PORT = getattr(settings, 'REDIS_PORT', 6379)
REDIS_DB = getattr(settings, 'REDIS_DB', 1)
REDIS_PASSWORD = getattr(settings, 'REDIS_PASSWORD', '')

if REDIS_PASSWORD:
    redis_url = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
else:
    redis_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# Create Redis client manager for horizontal scaling
# This allows multiple Daphne/Uvicorn instances to share state
mgr = socketio.AsyncRedisManager(redis_url)

# Create async Socket.IO server
sio = socketio.AsyncServer(
    async_mode='asgi',
    client_manager=mgr,
    cors_allowed_origins='*',  # TODO: Configure for production
    logger=True,
    engineio_logger=False,  # Set to True for debugging
    ping_interval=25,       # Send ping every 25 seconds
    ping_timeout=20,        # Disconnect after 20 seconds without pong
    max_http_buffer_size=10 * 1024 * 1024,  # 10MB max message size
)
