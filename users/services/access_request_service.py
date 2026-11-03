"""Access-request decisions and explicit, retryable mail delivery."""

import logging
import secrets
import uuid
from datetime import timedelta
from functools import partial
from smtplib import SMTPException
from urllib.parse import urljoin, urlsplit

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMessage
from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from users.access_request_constants import (
    APPROVAL_SALT,
    DELIVERY_STALE_MINUTES,
    DeliveryKind,
    DeliveryState,
    RequestKind,
    RequestState,
)
from users.access_request_models import (
    AccessRequest,
    AccessRequestDecision,
    AccessRequestDelivery,
    AccessRequestPolicy,
)
from users.constants import RoleChoice
from users.models import AccessCodeGroup, User

logger = logging.getLogger(__name__)


class AccessRequestsUnavailable(Exception):
    pass


class RequestAlreadyReviewed(Exception):
    pass


def reviewers_for(policy):
    return [
        u
        for u in policy.reviewers.filter(is_active=True, is_staff=True).exclude(
            email=""
        )
        if u.has_perm("users.change_accessrequest")
    ]


def validate_public_url(value, *, origin_only=False):
    parsed = urlsplit(value)
    local = settings.DEBUG and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "[::1]",
        "::1",
    }
    allowed_http = (
        parsed.scheme == "http"
        and f"http://{parsed.netloc.lower()}"
        in getattr(settings, "ACCESS_REQUEST_ALLOWED_HTTP_ORIGINS", [])
    )
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and (local or allowed_http)
    ):
        raise ValidationError(
            _("Use HTTPS or an explicitly allowlisted HTTP deployment origin.")
        )
    if (
        not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(
            _("Use a public URL without credentials, query parameters, or fragments.")
        )
    if origin_only and parsed.path not in {"", "/"}:
        raise ValidationError(
            _("Enter only the administrator origin, without /admin/.")
        )


def active_policy():
    policy = AccessRequestPolicy.objects.filter(pk=1, enabled=True).first()
    if (
        not policy
        or not policy.admin_base_url
        or not policy.registration_url
        or not reviewers_for(policy)
    ):
        raise AccessRequestsUnavailable
    try:
        validate_public_url(policy.admin_base_url, origin_only=True)
        validate_public_url(policy.registration_url)
    except ValidationError as error:
        raise AccessRequestsUnavailable from error
    return policy


def queue_delivery(access_request, kind, recipient, reviewer=None):
    delivery, created = AccessRequestDelivery.objects.get_or_create(
        request=access_request,
        kind=kind,
        recipient=recipient,
        decision_version=access_request.decision_version,
        defaults={"reviewer": reviewer},
    )
    if created:
        transaction.on_commit(partial(deliver, delivery.pk), robust=True)
    return delivery


@transaction.atomic
def request_access(*, name, email, reason):
    policy = active_policy()
    # The same generic response covers existing users and every request state.
    if User.objects.filter(email__iexact=email).exists():
        return
    access_request, created = AccessRequest.objects.get_or_create(
        email=email, defaults={"name": name, "reason": reason}
    )
    if created:
        for reviewer in reviewers_for(policy):
            queue_delivery(
                access_request, DeliveryKind.REVIEW, reviewer.email, reviewer
            )


@transaction.atomic
def request_registration_approval(user, name):
    policy = active_policy()
    access_request, created = AccessRequest.objects.select_for_update().get_or_create(
        email=user.email, defaults={"name": name, "reason": ""}
    )
    if (
        access_request.status != RequestState.PENDING
        or access_request.registered_user_id
    ):
        raise ValidationError(
            _(
                "An access request already exists. Use your invitation or contact the deployment administrator."
            )
        )
    access_request.kind = RequestKind.REGISTRATION
    access_request.registered_user = user
    access_request.save(update_fields=["kind", "registered_user"])
    if created:
        for reviewer in reviewers_for(policy):
            queue_delivery(
                access_request, DeliveryKind.REVIEW, reviewer.email, reviewer
            )


@transaction.atomic
def activate_registration(request_id):
    access_request = AccessRequest.objects.select_for_update().get(pk=request_id)
    if (
        access_request.status != RequestState.APPROVED
        or access_request.access_managed_in_users
        or access_request.activated_at
        or not access_request.registered_user_id
    ):
        return False
    user = User.objects.select_for_update().get(pk=access_request.registered_user_id)
    if user.is_deleted:
        return False
    group = user.access_code_group
    if not group or not group.is_active or group.is_expired:
        return False
    if not EmailAddress.objects.filter(
        user=user, email__iexact=user.email, verified=True
    ).exists():
        return False
    user.is_active = True
    user.save(update_fields=["is_active"])
    access_request.activated_at = timezone.now()
    access_request.save(update_fields=["activated_at"])
    queue_delivery(access_request, DeliveryKind.ACTIVATION, user.email)
    return True


def approval_token(access_request, reviewer):
    return signing.dumps(
        {
            "request": access_request.pk,
            "reviewer": reviewer.pk,
            "nonce": str(access_request.approval_nonce),
        },
        salt=APPROVAL_SALT,
        compress=True,
    )


def resolve_email_review(token, actor):
    policy = active_policy()
    if not policy.email_approval_enabled:
        raise PermissionDenied(
            _("Email approval is disabled. Review the request in Django admin.")
        )
    payload = signing.loads(
        token, salt=APPROVAL_SALT, max_age=timedelta(hours=policy.approval_link_hours)
    )
    if payload.get("reviewer") != actor.pk or actor.pk not in {
        u.pk for u in reviewers_for(policy)
    }:
        raise PermissionDenied
    access_request = AccessRequest.objects.get(
        pk=payload["request"], approval_nonce=payload["nonce"]
    )
    if access_request.status != RequestState.PENDING:
        raise RequestAlreadyReviewed
    return access_request


def request_account(access_request):
    """Lock only the account linked to this request or its redeemed invitation."""
    if access_request.registered_user_id:
        return User.objects.select_for_update().get(
            pk=access_request.registered_user_id
        )
    if access_request.code_group_id:
        return (
            User.objects.select_for_update()
            .filter(
                access_code_group_id=access_request.code_group_id,
                email__iexact=access_request.email,
            )
            .first()
        )
    return None


def grant_request(access_request, policy, user, *, actor):
    if user and user.is_deleted:
        raise ValidationError(_("Manage deleted accounts through user administration."))
    if user and (user.is_staff or user.is_superuser):
        if not actor.is_superuser:
            raise ValidationError(
                _(
                    "Only a superuser can record approval for an existing administrator account."
                )
            )
        if (
            not user.is_active
            or not EmailAddress.objects.filter(
                user=user, email__iexact=user.email, verified=True
            ).exists()
        ):
            raise ValidationError(
                _(
                    "This administrator account must already be active and email-verified. Manage it in Users first."
                )
            )
        # Record the existing grant; never replace its roles, flags or access group.
        access_request.access_managed_in_users = True
        access_request.registered_user = user
        access_request.activated_at = timezone.now()
        return
    if access_request.access_managed_in_users:
        raise ValidationError(
            _("This account's access is managed in Users. Review the account there.")
        )
    registration = access_request.kind == RequestKind.REGISTRATION or user is not None
    if registration and user is None:
        raise ValidationError(_("The registered account no longer exists."))
    if (
        not registration
        and User.objects.filter(email__iexact=access_request.email).exists()
    ):
        raise ValidationError(
            _(
                "An account already exists for this applicant. Review the account instead."
            )
        )
    group = access_request.code_group
    if group and group.current_usage and user is None:
        raise ValidationError(
            _(
                "The account created from this invitation no longer exists. Review the account instead."
            )
        )
    if group:
        # Bypass the legacy group signal: reapproval must not bypass email verification.
        AccessCodeGroup.objects.filter(pk=group.pk).update(is_active=True)
        group.is_active = True
        if not registration:
            group.access_code = secrets.token_urlsafe(32)
            group.expires_at = timezone.now() + timedelta(days=policy.invitation_days)
            group.save(update_fields=["access_code", "expires_at"])
    else:
        group = AccessCodeGroup.objects.create(
            access_code=secrets.token_urlsafe(32),
            max_capacity=1,
            default_role=RoleChoice.USER,
            model_group=policy.model_group,
            expires_at=(
                None
                if registration
                else timezone.now() + timedelta(days=policy.invitation_days)
            ),
            notes=_("Generated by administrator approval of an access request."),
        )
        access_request.code_group = group
        if registration:
            group.use_code()
            user.access_code_group = group
            user.save(update_fields=["access_code_group"])
    if user:
        if user.access_code_group_id != group.pk:
            raise ValidationError(
                _(
                    "The applicant's account access has changed. Review the account instead."
                )
            )
        access_request.registered_user = user


@transaction.atomic
def decide_request(
    request_id, *, actor, approve, email_token=None, revise=False, expected_version=None
):
    if (
        not actor.is_active
        or not actor.is_staff
        or not actor.has_perm("users.change_accessrequest")
    ):
        raise PermissionDenied
    if revise and email_token:
        raise PermissionDenied
    # Serialize decisions with changes to the configured approver membership.
    if not AccessRequestPolicy.objects.select_for_update().filter(pk=1).exists():
        raise AccessRequestsUnavailable
    policy = active_policy()
    if actor.pk not in {u.pk for u in reviewers_for(policy)}:
        raise PermissionDenied
    if email_token:
        linked_request = resolve_email_review(email_token, actor)
        if linked_request.pk != request_id:
            raise PermissionDenied
    access_request = AccessRequest.objects.select_for_update().get(pk=request_id)
    target = RequestState.APPROVED if approve else RequestState.DECLINED
    if revise:
        if (
            access_request.status == RequestState.PENDING
            or expected_version != access_request.decision_version
        ):
            raise ValidationError(
                _("The decision changed. Reload the request before trying again.")
            )
        if access_request.status == target:
            return False
    elif access_request.status != RequestState.PENDING:
        return False
    previous_status = access_request.status
    if access_request.code_group_id:
        access_request.code_group = AccessCodeGroup.objects.select_for_update().get(
            pk=access_request.code_group_id
        )
    user = request_account(access_request)
    if approve:
        grant_request(access_request, policy, user, actor=actor)
    else:
        if access_request.access_managed_in_users:
            raise ValidationError(
                _(
                    "This account's access is managed in Users. Disable it there rather than changing its signup decision."
                )
            )
        if user:
            if user.is_staff or user.is_superuser:
                raise ValidationError(
                    _("Manage administrator accounts through user administration.")
                )
            if (
                access_request.code_group_id
                and user.access_code_group_id != access_request.code_group_id
            ):
                raise ValidationError(
                    _(
                        "The applicant's account access has changed. Review the account instead."
                    )
                )
            user.is_active = False
            user.save(update_fields=["is_active"])
            access_request.registered_user = user
        if access_request.code_group_id:
            AccessCodeGroup.objects.filter(pk=access_request.code_group_id).update(
                is_active=False
            )
        access_request.activated_at = None
    access_request.status = target
    access_request.reviewed_by = actor
    access_request.reviewed_at = timezone.now()
    access_request.decision_version += 1
    if revise:
        access_request.approval_nonce = uuid.uuid4()
    access_request.save(
        update_fields=[
            "code_group",
            "access_managed_in_users",
            "registered_user",
            "activated_at",
            "status",
            "reviewed_by",
            "reviewed_at",
            "decision_version",
            "approval_nonce",
        ]
    )
    AccessRequestDecision.objects.create(
        request=access_request,
        version=access_request.decision_version,
        previous_status=previous_status,
        status=target,
        reviewer=actor,
    )
    if approve:
        if access_request.access_managed_in_users:
            queue_delivery(
                access_request, DeliveryKind.ACTIVATION, access_request.email
            )
        elif access_request.registered_user_id:
            if not activate_registration(access_request.pk):
                queue_delivery(
                    access_request, DeliveryKind.APPROVAL, access_request.email
                )
        else:
            queue_delivery(
                access_request, DeliveryKind.INVITATION, access_request.email
            )
    else:
        queue_delivery(access_request, DeliveryKind.DECLINE, access_request.email)
    return True


def review_cc(delivery):
    """Copy configured mailboxes once per request, unless they already review it."""
    reviews = delivery.request.deliveries.filter(kind=DeliveryKind.REVIEW)
    if reviews.order_by("pk").values_list("pk", flat=True).first() != delivery.pk:
        return []
    seen = {r.lower() for r in reviews.values_list("recipient", flat=True)}
    cc = []
    for address in settings.ACCESS_REQUEST_REVIEW_CC:
        if address.lower() not in seen:
            seen.add(address.lower())
            cc.append(address)
    return cc


def mail_for(delivery, policy):
    access_request = delivery.request
    if (
        delivery.kind != DeliveryKind.REVIEW
        and delivery.decision_version != access_request.decision_version
    ):
        raise ValidationError("decision_superseded")
    cc = []
    if delivery.kind == DeliveryKind.REVIEW:
        reviewer = delivery.reviewer
        if (
            not reviewer
            or reviewer.pk not in {u.pk for u in reviewers_for(policy)}
            or reviewer.email != delivery.recipient
        ):
            raise ValidationError("reviewer_unavailable")
        if access_request.status != RequestState.PENDING:
            raise ValidationError("request_already_reviewed")
        path = reverse("admin:users_accessrequest_change", args=[access_request.pk])
        if policy.email_approval_enabled:
            path = reverse(
                "admin:users_accessrequest_email_review",
                args=[approval_token(access_request, reviewer)],
            )
        subject = _("DARE access request awaiting review")
        introduction = _("A visitor has requested an invitation to sign up.")
        reason = _("Reason for access: %(reason)s\n") % {
            "reason": access_request.reason.strip() or _("Not provided.")
        }
        if access_request.kind == RequestKind.REGISTRATION:
            introduction = _("A new user has registered and is awaiting your approval.")
            reason = ""
        body = _(
            "Hello %(reviewer)s,\n\n%(introduction)s\n\nName: %(name)s\nEmail: %(email)s\n%(reason)s\nReview and approve or decline this request:\n%(url)s\n\nSign in as the administrator receiving this message. Opening the link does not approve the request."
        ) % {
            "reviewer": reviewer.get_full_name().strip() or reviewer.email,
            "introduction": introduction,
            "name": access_request.name,
            "email": access_request.email,
            "reason": reason,
            "url": policy.admin_base_url.rstrip("/") + path,
        }
        if policy.email_approval_enabled:
            body += _(
                "\n\nThis email-review link is assigned to %(reviewer)s. Other configured approvers can review with their own administrator account in Django admin:\n%(url)s"
            ) % {
                "reviewer": reviewer.email,
                "url": policy.admin_base_url.rstrip("/")
                + reverse("admin:users_accessrequest_change", args=[access_request.pk]),
            }
        if access_request.kind == RequestKind.REGISTRATION:
            cc = review_cc(delivery)
    elif delivery.kind == DeliveryKind.DECLINE:
        if access_request.status != RequestState.DECLINED:
            raise ValidationError("request_not_declined")
        subject = _("Your DARE access request has been declined")
        body = _(
            "Hello %(name)s,\n\nYour request for access to DARE has been declined following administrator review.\n\nReason: We are unable to grant access at this time.\n\nIf you believe this was a mistake, contact your deployment administrator to request a review."
        ) % {"name": access_request.name}
    elif delivery.kind == DeliveryKind.APPROVAL:
        if (
            access_request.status != RequestState.APPROVED
            or not access_request.registered_user_id
            or access_request.registered_user.is_deleted
        ):
            raise ValidationError("account_unavailable")
        subject = _("Your DARE signup has been approved")
        body = _(
            "An administrator has approved your signup.\n\nTo access your account, verify your email using the separate verification message sent when you registered, if you have not already done so. Then sign in at: %(url)s\n\nApproval does not replace email verification. If the verification email has not arrived, use Resend verification email on the signup pending page in the browser where you registered."
        ) % {"url": urljoin(policy.registration_url, "/login")}
    elif delivery.kind == DeliveryKind.ACTIVATION:
        if not access_request.activated_at or not access_request.registered_user_id:
            raise ValidationError("account_unavailable")
        user = access_request.registered_user
        if not user.is_active:
            raise ValidationError("account_inactive")
        subject = _("Your DARE account is ready")
        body = _(
            "Your email has been verified and an administrator has approved your account. Sign in at: %(url)s"
        ) % {"url": urljoin(policy.registration_url, "/login")}
    else:
        group = access_request.code_group
        if (
            access_request.status != RequestState.APPROVED
            or not group
            or not group.is_available
        ):
            raise ValidationError("invitation_unavailable")
        subject = _("Your DARE signup invitation")
        body = _(
            "Your request for access has been approved.\n\nRegister at: %(url)s\nAccess code: %(code)s\nExpires: %(expires)s\n\nThis code is single-use and restricted to this email address. After registration, follow the separate email verification link before signing in. Contact your deployment administrator if verification mail does not arrive."
        ) % {
            "url": policy.registration_url,
            "code": group.access_code,
            "expires": group.expires_at.isoformat(),
        }
    if (
        access_request.decisions.filter(version=delivery.decision_version)
        .exclude(previous_status=RequestState.PENDING)
        .exists()
        and delivery.kind != DeliveryKind.REVIEW
    ):
        subject = _("DARE access decision updated: %(status)s") % {
            "status": access_request.get_status_display()
        }
    if getattr(settings, "EMAIL_REPLY_TO", ""):
        body += _(
            "\n\nQuestions? Reply to this email to contact our team at %(email)s."
        ) % {"email": settings.EMAIL_REPLY_TO}
    message = EmailMessage(str(subject), str(body), to=[delivery.recipient], cc=cc)
    message.dare_sensitive = True
    return message


def deliver(delivery_id):
    """Claim before I/O so concurrent retries cannot send the same delivery together."""
    with transaction.atomic():
        delivery = AccessRequestDelivery.objects.select_for_update().get(pk=delivery_id)
        stale = (
            delivery.attempted_at
            and delivery.attempted_at
            < timezone.now() - timedelta(minutes=DELIVERY_STALE_MINUTES)
        )
        if delivery.state == DeliveryState.SENT or (
            delivery.state == DeliveryState.SENDING and not stale
        ):
            return False
        delivery.state = DeliveryState.SENDING
        delivery.attempts += 1
        delivery.attempted_at = timezone.now()
        delivery.failure_reason = ""
        delivery.save(
            update_fields=["state", "attempts", "attempted_at", "failure_reason"]
        )
    failure = ""
    try:
        policy = active_policy()
        backend = settings.EMAIL_BACKEND
        if backend.endswith(
            ("LoggingConsoleEmailBackend", "console.EmailBackend", "dummy.EmailBackend")
        ):
            failure = "mail_backend_not_delivering"
        elif mail_for(delivery, policy).send() != 1:
            failure = "mail_backend_rejected"
    except AccessRequestsUnavailable:
        failure = "access_requests_unavailable"
    except ValidationError as error:
        failure = error.messages[0]
    except (SMTPException, OSError, DatabaseError):
        failure = "mail_delivery_failed"
    AccessRequestDelivery.objects.filter(pk=delivery_id).update(
        state=DeliveryState.FAILED if failure else DeliveryState.SENT,
        failure_reason=failure,
        sent_at=None if failure else timezone.now(),
    )
    if failure:
        logger.warning("Access request delivery %s failed (%s)", delivery_id, failure)
    return not failure
