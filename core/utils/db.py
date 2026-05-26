import logging

from django.db import OperationalError, close_old_connections

logger = logging.getLogger(__name__)


def db_reconnect_on_stale(fn, *args, **kwargs):
    """Call fn(), reconnecting once on a stale psycopg connection."""
    try:
        return fn(*args, **kwargs)
    except OperationalError as e:
        logger.warning("Stale DB connection — reconnecting and retrying. %s", e)
        close_old_connections()
        return fn(*args, **kwargs)
