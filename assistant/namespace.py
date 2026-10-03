"""Socket.IO transport for the platform assistant.

Client → server:
    send_message {message, path}   ack {ok} or {error}
    stop_generation {}             cancels the in-flight answer

Server → client, all on the ``assistant`` event, discriminated by ``type``:
    assistant_turn_started {question, reply}
    assistant_stream       {messageId, content}   cumulative text
    tool_call_*            the shared tool-loop lifecycle events
    assistant_message      {message}              the persisted final reply
    assistant_error        {code, message}
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import jwt
import socketio
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from djangorestframework_camel_case.util import camelize

from assistant.api.serializers import (
    AssistantMessageSerializer,
    AssistantSendSerializer,
)
from assistant.constants import SOCKET_EVENT, SOCKET_NAMESPACE
from assistant.services.thread_service import (
    AssistantLimitReached,
    AssistantUnavailable,
)
from assistant.services.turn_service import AssistantTurnService
from conversations.socket_server import sio
from core.utils.db import db_reconnect_on_stale

logger = logging.getLogger(__name__)
User = get_user_model()

UNAVAILABLE_MESSAGE = "The assistant is unavailable right now. Please try again later."


class AssistantNamespace(socketio.AsyncNamespace):
    def __init__(self) -> None:
        super().__init__(namespace=SOCKET_NAMESPACE)
        self.turns = AssistantTurnService()
        self.users: Dict[str, Any] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

    async def on_connect(self, sid: str, environ: dict, auth: Optional[dict] = None):
        token = (auth or {}).get("token")
        if not token:
            raise socketio.exceptions.ConnectionRefusedError("JWT token required")
        try:
            decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        except jwt.InvalidTokenError as error:
            raise socketio.exceptions.ConnectionRefusedError("Invalid token") from error
        user = await self._get_user(decoded.get("user_id"))
        if user is None:
            raise socketio.exceptions.ConnectionRefusedError("User not found")
        self.users[sid] = user
        return True

    async def on_disconnect(self, sid: str, reason: Optional[str] = None):
        self.users.pop(sid, None)
        task = self.tasks.pop(sid, None)
        if task and not task.done():
            task.cancel()

    async def on_send_message(self, sid: str, data: Any) -> dict:
        user = self.users.get(sid)
        if user is None:
            return {"error": "Not authenticated"}
        if sid in self.tasks and not self.tasks[sid].done():
            return {"error": "The assistant is still answering."}
        serializer = AssistantSendSerializer(
            data=data if isinstance(data, dict) else {}
        )
        if not serializer.is_valid():
            return {"error": "Invalid message", "details": serializer.errors}
        payload = serializer.validated_data
        self.tasks[sid] = asyncio.create_task(
            self._run_turn(sid, user, payload["message"], payload["path"])
        )
        return {"ok": True}

    async def on_stop_generation(self, sid: str, data: Any = None) -> dict:
        task = self.tasks.get(sid)
        if task and not task.done():
            task.cancel()
        return {"ok": True}

    async def _run_turn(self, sid: str, user, question: str, path: str) -> None:
        async def emit(payload: Dict[str, Any]) -> None:
            await sio.emit(
                SOCKET_EVENT, camelize(payload), to=sid, namespace=SOCKET_NAMESPACE
            )

        try:
            turn = await self.turns.begin(user, question, path)
        except AssistantLimitReached as error:
            await emit(
                {
                    "type": "assistant_error",
                    "code": "daily_limit",
                    "message": str(error),
                }
            )
            return
        except AssistantUnavailable as error:
            logger.error("[assistant] unavailable: %s", error)
            await emit(
                {
                    "type": "assistant_error",
                    "code": "unavailable",
                    "message": UNAVAILABLE_MESSAGE,
                }
            )
            return

        await emit(
            {
                "type": "assistant_turn_started",
                "question": await _serialize(turn.question),
                "reply": await _serialize(turn.reply),
            }
        )
        reply = await self.turns.complete(turn, user, path, emit)
        await emit({"type": "assistant_message", "message": await _serialize(reply)})

    @sync_to_async
    def _get_user(self, user_id: Optional[int]):
        if not user_id:
            return None
        try:
            return db_reconnect_on_stale(User.objects.get, id=user_id)
        except User.DoesNotExist:
            return None


@sync_to_async
def _serialize(message) -> dict:
    # Serialising reads the message's proposals from the database.
    return AssistantMessageSerializer(message).data


assistant_namespace = AssistantNamespace()
