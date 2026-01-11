"""
ASGI config for dare project.

It exposes the ASGI callable as a module-level variable named ``application``.

Supports both:
- Socket.IO (new) at /socket.io/ - single persistent connection model
- Django Channels WebSockets (legacy) at /ws/ - per-conversation connections

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/asgi/
"""

import os
from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

import django
django.setup()

import socketio
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from conversations.middleware import JwtAuthMiddleware
from conversations.routing import websocket_urlpatterns

# Import Socket.IO server and register all namespaces
from conversations.socket_server import sio, register_namespaces

# Register all Socket.IO namespaces (chat, workflow, etc.)
register_namespaces()

# Get Django ASGI application
django_asgi_app = get_asgi_application()

# Create legacy Django Channels WebSocket app (for backwards compatibility)
channels_websocket_app = JwtAuthMiddleware(URLRouter(websocket_urlpatterns))

# Create combined ASGI application
# Socket.IO handles /socket.io/ paths
# Django Channels handles legacy /ws/ paths
# Django handles all HTTP requests
application = socketio.ASGIApp(
    sio,
    other_asgi_app=ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": channels_websocket_app,
        }
    ),
    socketio_path='socket.io',
)
