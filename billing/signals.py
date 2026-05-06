from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.conf import settings
from decimal import Decimal
from users.models import User
from billing.models import (
    Wallet,
    UserWalletPreference,
    LiteLLMKey,
)
from billing.constants import (
    UserWalletPreferenceTypeChoice,
)
from billing.litellm_models_service import invalidate as invalidate_litellm_probe
from api_keys.constants import BillingModeChoice
from api_keys.models import UserProviderAPIKey


# Reentrancy guard for the User.billing_mode <-> UserWalletPreference bridge:
# both signals write to the other model, which would otherwise loop forever.
_BRIDGE_GUARD = set()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_wallet(sender, instance, created, **kwargs):
    """
    Create a wallet when a new user is registered.
    Default starting balance is $0.00; initial credit is applied explicitly
    during registration (either via Access Code Group setting or $5 default).
    """
    if created:
        Wallet.objects.create(user=instance, balance=Decimal('0.00'))


def _legacy_to_preference(billing_mode: str) -> str:
    """Map legacy User.billing_mode value to UserWalletPreference.active_wallet_type."""
    if billing_mode == BillingModeChoice.OWN_API:
        return UserWalletPreferenceTypeChoice.BYO
    if billing_mode == BillingModeChoice.LITELLM:
        return UserWalletPreferenceTypeChoice.LITELLM
    return UserWalletPreferenceTypeChoice.DARE


def _preference_to_legacy(active_wallet_type: str) -> str:
    """Map UserWalletPreference.active_wallet_type to legacy User.billing_mode."""
    if active_wallet_type == UserWalletPreferenceTypeChoice.BYO:
        return BillingModeChoice.OWN_API
    if active_wallet_type == UserWalletPreferenceTypeChoice.LITELLM:
        return BillingModeChoice.LITELLM
    return BillingModeChoice.WALLET


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def mirror_billing_mode_to_preference(sender, instance, created, **kwargs):
    """
    When User.billing_mode is set via the existing /billing-mode/ endpoint or
    Django admin, mirror the choice into UserWalletPreference so the new
    wallet_router stays in sync with the legacy field.

    Note: setting active_wallet_type=BYO via this bridge does NOT pick a ref_id —
    if the user has a pre-existing UserProviderAPIKey we leave the existing
    UserWalletPreference.active_wallet_ref_id intact. If they don't have one,
    the BYO mirror would fail full_clean(), so we fall back to DARE silently.
    """
    if created:
        # Lazy creation handled by get_or_create_for on first read; nothing to mirror.
        return

    key = ("user", instance.pk)
    if key in _BRIDGE_GUARD:
        return

    pref = UserWalletPreference.objects.filter(user=instance).first()
    if pref is None:
        return

    target_type = _legacy_to_preference(instance.billing_mode)
    if pref.active_wallet_type == target_type:
        return

    _BRIDGE_GUARD.add(key)
    try:
        # If we can't satisfy the target's invariants (e.g. BYO with flag off,
        # no BYO key on file), fall back to DARE rather than raise — the legacy
        # field accepts values that the new model rejects.
        try:
            pref.active_wallet_type = target_type
            pref.active_wallet_ref_id = None
            pref.save(update_fields=["active_wallet_type", "active_wallet_ref_id", "updated_at"])
        except Exception:
            pref.reset_to_dare()
    finally:
        _BRIDGE_GUARD.discard(key)


@receiver(post_save, sender=UserWalletPreference)
def mirror_preference_to_billing_mode(sender, instance, created, **kwargs):
    """
    When UserWalletPreference is updated, mirror the chosen wallet type back
    into User.billing_mode so legacy callers (api_key_service, admin filters,
    serializers) see a consistent value during the transition window.
    """
    key = ("pref", instance.user_id)
    if key in _BRIDGE_GUARD:
        return

    target_legacy = _preference_to_legacy(instance.active_wallet_type)
    user = instance.user
    if user.billing_mode == target_legacy:
        return

    _BRIDGE_GUARD.add(key)
    try:
        user.billing_mode = target_legacy
        user.save(update_fields=["billing_mode"])
    finally:
        _BRIDGE_GUARD.discard(key)


# === Cascade-reset active wallet when its underlying key is deleted ========
#
# When a user (or admin) deletes the credential row their UserWalletPreference
# points at, the preference would dangle. Reset it to DARE so the next request
# resolves cleanly without going through the wallet_router self-heal path.

@receiver(pre_delete, sender=UserProviderAPIKey)
def reset_pref_on_byo_delete(sender, instance, **kwargs):
    """
    User deleted a BYO key. Reset their preference back to DARE if either:
      - the deleted key was their explicit `ref_id` (legacy specific-key BYO), or
      - they are on collective BYO (`ref_id is None`) and this was their last
        BYO key — otherwise the wallet picker would show "BYO Wallet active"
        with no key rows, which is incoherent.
    """
    pref = UserWalletPreference.objects.filter(
        user=instance.user,
        active_wallet_type=UserWalletPreferenceTypeChoice.BYO,
    ).first()
    if pref is None:
        return

    if pref.active_wallet_ref_id == str(instance.pk):
        pref.reset_to_dare()
        return

    if pref.active_wallet_ref_id is None:
        remaining = (
            UserProviderAPIKey.active_objects
            .filter(user=instance.user)
            .exclude(pk=instance.pk)
            .exclude(api_key__isnull=True)
            .exclude(api_key="")
            .exists()
        )
        if not remaining:
            pref.reset_to_dare()


@receiver(pre_delete, sender=LiteLLMKey)
def reset_pref_on_litellm_delete(sender, instance, **kwargs):
    """
    Admin or user deleted a LiteLLM key — if any user has it set as active,
    reset their preference. For ADMIN_GROUP keys this can affect every member
    of the cohort.
    """
    invalidate_litellm_probe(instance.pk)
    qs = UserWalletPreference.objects.filter(
        active_wallet_type=UserWalletPreferenceTypeChoice.LITELLM,
        active_wallet_ref_id=str(instance.pk),
    )
    for pref in qs:
        pref.reset_to_dare()


@receiver(post_save, sender=LiteLLMKey)
def invalidate_litellm_probe_on_save(sender, instance, **kwargs):
    """Drop the cached probe when the key's URL or secret changes."""
    invalidate_litellm_probe(instance.pk)
