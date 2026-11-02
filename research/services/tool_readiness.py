"""Whether Hermes is connected to DARE's gateway for a project, as the gateway saw it.

Any request Hermes makes for a project (its initial tools/list, a call, the
keepalive ping) means the profile is served with its DARE tools.
"""

from django.core.cache import cache

_TTL = 60 * 60 * 24 * 30


def _key(project_id):
    return f"research-tools-connected:{project_id}"


def mark_tools_connected(project_id):
    cache.set(_key(project_id), True, _TTL)


def tools_connected(project_id):
    return cache.get(_key(project_id)) is not None
