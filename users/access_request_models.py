"""Persistent access requests, administrator policy, and mail delivery records."""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from users.access_request_constants import (
    DeliveryKind,
    DeliveryState,
    RequestKind,
    RequestState,
)


class AccessRequestPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Enable access requests and administrator approval for code-free DARE signup."
        ),
    )
    email_approval_enabled = models.BooleanField(
        default=False,
        help_text=_(
            "Allow authorized administrators to review requests through emailed links. Disabling this also disables links already sent."
        ),
    )
    reviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Request approvers"),
        blank=True,
        related_name="access_review_policies",
        limit_choices_to={"is_active": True, "is_staff": True},
        help_text=_(
            "Only these administrators may approve, decline, or change decisions. Each needs permission to change access requests."
        ),
    )
    admin_base_url = models.URLField(
        blank=True,
        help_text=_(
            "Public administrator origin, including port if needed, for example https://admin.example.org. Do not include /admin/."
        ),
    )
    registration_url = models.URLField(
        blank=True,
        help_text=_(
            "Public signup page, for example https://dare.example.org/register."
        ),
    )
    approval_link_hours = models.PositiveSmallIntegerField(
        default=48,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
        help_text=_("Lifetime of an email approval link in hours (1–168)."),
    )
    invitation_days = models.PositiveSmallIntegerField(
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text=_("Invitation code lifetime in days (1–30)."),
    )
    model_group = models.ForeignKey(
        "conversations.ModelGroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_request_policies",
        help_text=_(
            "Model access for generated invitations. Blank retains the existing default: all active models."
        ),
    )

    class Meta:
        verbose_name = _("Access request settings")
        verbose_name_plural = _("Access request settings")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="one_access_request_policy"
            )
        ]

    def __str__(self):
        return str(_("Access request settings"))


class AccessRequest(models.Model):
    kind = models.CharField(
        max_length=12,
        choices=RequestKind.choices,
        default=RequestKind.INVITATION,
        help_text=_(
            "Request a signup code or approve an account registered without a code."
        ),
    )
    registered_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signup_request",
        help_text=_(
            "Applicant account; access requires approval and email verification."
        ),
    )
    activated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "Activation under the current approval; decision changes are retained in history."
        ),
    )
    email = models.EmailField(
        unique=True,
        help_text=_(
            "Applicant's normalized email address. Duplicate requests do not create more notifications."
        ),
    )
    name = models.CharField(max_length=255, help_text=_("Applicant's name."))
    reason = models.CharField(
        max_length=1000, blank=True, help_text=_("Why the applicant needs access.")
    )
    status = models.CharField(
        max_length=10,
        choices=RequestState.choices,
        default=RequestState.PENDING,
        db_index=True,
        help_text=_("Administrator's decision."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_access_requests",
        help_text=_("Administrator who made the decision."),
    )
    code_group = models.OneToOneField(
        "users.AccessCodeGroup",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="access_request",
        help_text=_("Email-bound, single-use invitation created on approval."),
    )
    access_managed_in_users = models.BooleanField(
        db_default=False,
        default=False,
        editable=False,
        verbose_name=_("Access managed in Users"),
        help_text=_(
            "Existing administrator access is managed in Users; this request records approval only."
        ),
    )
    decision_version = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text=_("Version of the current administrator decision."),
    )
    approval_nonce = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=RequestState.PENDING,
                        reviewed_at__isnull=True,
                        code_group__isnull=True,
                    )
                    | (
                        models.Q(
                            status=RequestState.APPROVED, reviewed_at__isnull=False
                        )
                        & (
                            models.Q(code_group__isnull=False)
                            | models.Q(access_managed_in_users=True)
                        )
                    )
                    | models.Q(
                        status=RequestState.DECLINED,
                        reviewed_at__isnull=False,
                    )
                ),
                name="access_request_decision_consistent",
            )
        ]

    def __str__(self):
        return f"{self.email} ({self.get_status_display()})"


class AccessRequestDecision(models.Model):
    request = models.ForeignKey(
        AccessRequest,
        on_delete=models.CASCADE,
        related_name="decisions",
        help_text=_("Request whose decision changed."),
    )
    version = models.PositiveIntegerField(help_text=_("Saved decision version."))
    previous_status = models.CharField(
        max_length=10,
        choices=RequestState.choices,
        help_text=_("Status before this decision."),
    )
    status = models.CharField(
        max_length=10,
        choices=RequestState.choices,
        help_text=_("Status after this decision."),
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="access_request_decisions",
        help_text=_("Administrator who saved this decision."),
    )
    decided_at = models.DateTimeField(
        auto_now_add=True, help_text=_("Decision timestamp.")
    )

    class Meta:
        ordering = ["version"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "version"],
                name="unique_access_request_decision_version",
            )
        ]


class AccessRequestDelivery(models.Model):
    request = models.ForeignKey(
        AccessRequest,
        on_delete=models.CASCADE,
        related_name="deliveries",
        help_text=_("Request this email concerns."),
    )
    kind = models.CharField(
        max_length=10,
        choices=DeliveryKind.choices,
        help_text=_(
            "Review, invitation, approval, account-ready, or decline notification."
        ),
    )
    decision_version = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text=_("Decision this notification describes."),
    )
    recipient = models.EmailField(help_text=_("Intended email recipient."))
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_request_deliveries",
        help_text=_("Administrator addressed by a review notification."),
    )
    state = models.CharField(
        max_length=10,
        choices=DeliveryState.choices,
        default=DeliveryState.PENDING,
        help_text=_(
            "Mail backend submission result; sent is not proof of inbox delivery."
        ),
    )
    attempts = models.PositiveIntegerField(default=0)
    attempted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(
        max_length=100,
        blank=True,
        help_text=_(
            "Safe diagnostic code. Provider errors and invitation secrets are not stored here."
        ),
    )

    class Meta:
        verbose_name = _("Access request delivery")
        verbose_name_plural = _("Access request deliveries")
        constraints = [
            models.UniqueConstraint(
                fields=["request", "kind", "recipient", "decision_version"],
                name="unique_access_request_delivery_decision",
            )
        ]

    def __str__(self):
        return (
            f"{self.get_kind_display()} → {self.recipient}: {self.get_state_display()}"
        )
