"""Default wallet for members of an access-code group.

A group can be issued a LiteLLM key (``source=ADMIN_GROUP``). The owner
provisions it so the whole cohort bills to their gateway, so members should
route through it without having to find it in the wallet switcher themselves.

This module owns that one decision — which key a group routes through, and
when a member adopts it. Adoption sets a *default*, never a lock: members keep
their own wallet and its credit, and can switch back to DARE or a BYO key at
any time.
"""

import logging
from typing import Optional

from billing.constants import LiteLLMKeySourceChoice, UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey, UserWalletPreference
from feature_flags.services import is_flag_enabled_for_user

logger = logging.getLogger(__name__)


def group_default_key(group) -> Optional[LiteLLMKey]:
    """The key a group routes through, or None when there isn't exactly one.

    Ambiguity is deliberately not resolved: billing a cohort to the wrong
    institutional account is worse than asking one member to choose once.
    """
    if group is None:
        return None

    keys = list(
        LiteLLMKey.objects.filter(
            source=LiteLLMKeySourceChoice.ADMIN_GROUP,
            source_group=group,
        ).exclude(api_key="")[:2]
    )
    usable = [key for key in keys if not getattr(key, "is_expired", False)]

    if len(usable) != 1:
        if len(usable) > 1:
            logger.info(
                "Access code group %s has multiple LiteLLM keys; leaving members "
                "on their existing wallet rather than guessing.",
                group.pk,
            )
        return None
    return usable[0]


def adopt_group_wallet(user) -> bool:
    """Point a member's wallet at their group's key. Returns whether it moved.

    Only members sitting on the DARE default are moved, so a deliberate BYO or
    personal-key choice is never overwritten. A member who switched back to
    DARE is re-defaulted the next time their group is issued a key — that is a
    provisioning event, and the switch remains available to them.
    """
    group = getattr(user, "access_code_group", None)
    key = group_default_key(group)
    if key is None:
        return False

    if not is_flag_enabled_for_user(user, "enable_litellm_wallet"):
        return False

    pref = UserWalletPreference.get_or_create_for(user)
    if pref.active_wallet_type != UserWalletPreferenceTypeChoice.DARE:
        return False

    pref.active_wallet_type = UserWalletPreferenceTypeChoice.LITELLM
    pref.active_wallet_ref_id = str(key.pk)
    pref.save(
        update_fields=["active_wallet_type", "active_wallet_ref_id", "updated_at"]
    )
    logger.info(
        "User %s defaulted to group LiteLLM key %s via access code group %s.",
        user.pk,
        key.pk,
        group.pk,
    )
    return True
