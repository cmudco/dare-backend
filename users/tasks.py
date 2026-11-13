"""Background delivery entrypoints for signup review."""

from allauth.account.models import EmailAddress
from django.http import HttpRequest

from config import env
from users.services.access_request_service import deliver


def deliver_access_request(delivery_id):
    return deliver(delivery_id)


def send_signup_verification(email_id):
    email = EmailAddress.objects.select_related("user").get(pk=email_id)
    if email.verified or email.user.is_deleted:
        return
    request = HttpRequest()
    request.META["HTTP_ORIGIN"] = env.DARE_FRONTEND_URL
    email.send_confirmation(request, signup=True)
