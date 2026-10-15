"""Tell group owners when a member is blocked by their gateway limit, and the
member when a limit change lets them send again."""

from typing import Optional

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from billing.caps import SpendLimit
from billing.models import MemberLimitAlert
from config.env import DARE_FRONTEND_URL
from notifications.constants import (
    NotificationAction,
    NotificationCategory,
    NotificationDeliveryType,
)
from notifications.models import Notification
from users.constants import AuthSourceChoice
from users.models import AccessCodeGroup, User

GROUP_WALLET_PATH = "/group-wallet"


def alert_owner_limit_reached(
    group: AccessCodeGroup, member: User, spend_limit: SpendLimit
) -> None:
    """Open the member's block and notify the owner; no-op while one is open."""
    owner = group.group_owner
    if owner is None:
        return
    with transaction.atomic():
        created = MemberLimitAlert.objects.get_or_create(
            group=group,
            member=member,
            resolved_at__isnull=True,
            defaults={"limit": spend_limit.limit},
        )[1]
        if not created:
            return
        group_wallet_url = _group_wallet_url()
        Notification.objects.create(
            user=owner,
            title=_("A member reached their AI gateway limit"),
            message=_(
                "{member} used their ${limit} allowance on {group}'s AI gateway "
                "and can't send messages until you raise their limit."
            ).format(
                member=_member_label(member),
                limit=f"{spend_limit.limit:.2f}",
                group=group.access_code,
            ),
            category=NotificationCategory.WARNING,
            delivery_type=NotificationDeliveryType.PANEL,
            action_type=(
                NotificationAction.NAVIGATE
                if group_wallet_url
                else NotificationAction.NONE
            ),
            action_url=group_wallet_url,
            source=AuthSourceChoice.DARE,
        )


def close_limit_alert(
    alert: MemberLimitAlert, spend_limit: Optional[SpendLimit], *, notify: bool
) -> None:
    """Close a block; when `notify`, tell the member they can send again.

    `spend_limit` is the member's limit now, or None when they have none.
    """
    with transaction.atomic():
        closed = MemberLimitAlert.objects.filter(
            pk=alert.pk, resolved_at__isnull=True
        ).update(resolved_at=timezone.now())
        if not closed or not notify:
            return
        group = alert.group.access_code
        Notification.objects.create(
            user=alert.member,
            title=_("You can use the AI gateway again"),
            message=(
                _(
                    "Your limit on {group}'s AI gateway was raised to ${limit}. "
                    "You can send messages again."
                ).format(group=group, limit=f"{spend_limit.limit:.2f}")
                if spend_limit is not None
                else _(
                    "Your limit on {group}'s AI gateway was removed. "
                    "You can send messages again."
                ).format(group=group)
            ),
            category=NotificationCategory.SUCCESS,
            delivery_type=NotificationDeliveryType.PANEL,
            source=AuthSourceChoice.DARE,
        )


def _member_label(member: User) -> str:
    name = member.get_full_name().strip()
    return f"{name} ({member.email})" if name else member.email


def _group_wallet_url() -> Optional[str]:
    if not DARE_FRONTEND_URL:
        return None
    return DARE_FRONTEND_URL.rstrip("/") + GROUP_WALLET_PATH
