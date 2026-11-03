from django.db import models
from django.utils.translation import gettext_lazy as _


class RequestState(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    DECLINED = "DECLINED", _("Declined")


class DeliveryKind(models.TextChoices):
    REVIEW = "REVIEW", _("Administrator notification")
    INVITATION = "INVITATION", _("Applicant invitation")
    APPROVAL = "APPROVAL", _("Registration approved")
    ACTIVATION = "ACTIVATION", _("Account activated")
    DECLINE = "DECLINE", _("Request declined")


class RequestKind(models.TextChoices):
    INVITATION = "INVITATION", _("Signup invitation")
    REGISTRATION = "REGISTRATION", _("Registered account approval")


class DeliveryState(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    SENDING = "SENDING", _("Sending")
    SENT = "SENT", _("Submitted to mail backend")
    FAILED = "FAILED", _("Failed")


APPROVAL_SALT = "users.access-request-approval"
ACCESS_REQUEST_THROTTLE_RATE = "5/hour"
DELIVERY_STALE_MINUTES = 5
