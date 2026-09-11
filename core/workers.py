"""RQ worker defaults shared by document, memory and scheduler queues."""

from django.conf import settings
from rq import Worker


class InspectableWorker(Worker):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("default_result_ttl", settings.RQ["DEFAULT_RESULT_TTL"])
        super().__init__(*args, **kwargs)
