"""Running AI turns, shared across ASGI workers through the Redis cache.

A turn runs on the worker that received the send, but the browser may
reconnect to any worker. Each running turn keeps a short-lived key alive
(``message id -> conversation id``); a missing key means the turn has
ended or died with its worker. Stop requests travel the same way: any
worker can set a stop key, and the owning worker's heartbeat acts on it.
"""

from typing import Iterable, Set

from asgiref.sync import sync_to_async
from django.core.cache import cache

HEARTBEAT_SECONDS = 0.5
LIVE_TTL_SECONDS = 5
STOP_TTL_SECONDS = 60


def _live_key(message_id: int) -> str:
    return f"chat_generation:{message_id}"


def _stop_key(message_id: int) -> str:
    return f"chat_generation_stop:{message_id}"


# Redis I/O must not queue behind the thread-sensitive Django ORM executor.
_cache_get = sync_to_async(cache.get, thread_sensitive=False)
_cache_get_many = sync_to_async(cache.get_many, thread_sensitive=False)
_cache_set = sync_to_async(cache.set, thread_sensitive=False)
_cache_delete_many = sync_to_async(cache.delete_many, thread_sensitive=False)


async def mark_live(message_id: int, conversation_id: str) -> None:
    await _cache_set(_live_key(message_id), str(conversation_id), LIVE_TTL_SECONDS)


async def clear(message_id: int) -> None:
    await _cache_delete_many([_live_key(message_id), _stop_key(message_id)])


async def live_ids(conversation_id: str, message_ids: Iterable[int]) -> Set[int]:
    """The subset of ``message_ids`` still generating in this conversation."""
    keys = {_live_key(mid): mid for mid in message_ids}
    if not keys:
        return set()
    found = await _cache_get_many(list(keys))
    return {keys[key] for key, conv in found.items() if conv == str(conversation_id)}


async def request_stop(conversation_id: str, message_id: int) -> bool:
    """Ask the worker running ``message_id`` to stop it; False when nothing runs."""
    if not await live_ids(conversation_id, [message_id]):
        return False
    await _cache_set(_stop_key(message_id), True, STOP_TTL_SECONDS)
    return True


async def stop_requested(message_id: int) -> bool:
    return bool(await _cache_get(_stop_key(message_id)))


def is_interrupted(text: str, has_files: bool) -> bool:
    """A reply with no running turn that never got content: its turn died."""
    return not text.strip() and not has_files
