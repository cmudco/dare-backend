"""Tell a group owner when one of their members runs out of gateway allowance."""

from django.db import transaction
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
    """Notify the owner once per member and limit; repeat calls are no-ops."""
    owner = group.group_owner
    if owner is None:
        return
    with transaction.atomic():
        created = MemberLimitAlert.objects.get_or_create(
            group=group, member=member, limit=spend_limit.limit
        )[1]
        if not created:
            return
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
                if DARE_FRONTEND_URL
                else NotificationAction.NONE
            ),
            action_url=_group_wallet_url(),
            source=AuthSourceChoice.DARE,
        )


def _member_label(member: User) -> str:
    name = member.get_full_name().strip()
    return f"{name} ({member.email})" if name else member.email


def _group_wallet_url():
    if not DARE_FRONTEND_URL:
        return None
    return DARE_FRONTEND_URL.rstrip("/") + GROUP_WALLET_PATH
