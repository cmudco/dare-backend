"""Background delivery entrypoints for signup review."""

from users.services.access_request_service import deliver


def deliver_access_request(delivery_id):
    return deliver(delivery_id)
