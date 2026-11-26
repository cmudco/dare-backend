"""Email ownership and administrator approval are separate activation gates."""

from allauth.account.signals import email_confirmed
from django.dispatch import receiver

from users.services.access_request_service import activate_verified_registration


@receiver(email_confirmed, dispatch_uid="users.activate_approved_registration")
def activate_approved_registration(sender, request, email_address, **kwargs):
    user = email_address.user
    if email_address.email.lower() == user.email.lower():
        activate_verified_registration(user)
