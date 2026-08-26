"""
ASGI config for dare project.

It exposes the ASGI callable as a module-level variable named ``application``.

Socket.IO handles all real-time WebSocket communication.

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
from django.core.asgi import get_asgi_application

# Import Socket.IO server and register all namespaces
from conversations.socket_server import register_namespaces, sio

# Register all Socket.IO namespaces (chat, workflow, etc.)
register_namespaces()

# Get Django ASGI application
django_asgi_app = get_asgi_application()

# Socket.IO only - handles all WebSocket communication
socketio_app = socketio.ASGIApp(
    sio,
    other_asgi_app=django_asgi_app,
    socketio_path="socket.io",
)


async def application(scope, receive, send):
    """ASGI entrypoint that answers the lifespan protocol itself.

    uvicorn opens a lifespan channel at startup. Django's handler refuses the
    scope outright, and the refusal surfaced as hundreds of production errors
    that buried real ones. Nothing here needs startup or shutdown hooks, so
    acknowledge the protocol and let every other scope through untouched.
    """
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await socketio_app(scope, receive, send)
