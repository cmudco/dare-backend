"""Email ownership and administrator approval are separate activation gates."""

from allauth.account.signals import email_confirmed
from django.dispatch import receiver

from users.access_request_models import AccessRequest
from users.services.access_request_service import activate_registration


@receiver(email_confirmed, dispatch_uid="users.activate_approved_registration")
def activate_approved_registration(sender, request, email_address, **kwargs):
    user = email_address.user
    if email_address.email.lower() != user.email.lower():
        return
    request_id = (
        AccessRequest.objects.filter(registered_user=user)
        .values_list("pk", flat=True)
        .first()
    )
    if request_id is not None:
        activate_registration(request_id)
