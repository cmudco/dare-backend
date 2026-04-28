import uuid
from decimal import Decimal
from django.db import models, transaction as db_transaction
from django.db.models import Q
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from billing.constants import (
    TransactionTypeChoice,
    TransactionSourceChoice,
    LiteLLMKeySourceChoice,
    UserWalletPreferenceTypeChoice,
    DEFAULT_REFILL_AMOUNT,
    DEFAULT_REFILL_PERIOD_DAYS,
)
from common.models import TimeStampMixin
from conversations.models import LLM
from core.fields import EncryptedCharField
from users.models import User, AccessCodeGroup
from users.constants import AuthSourceChoice
from api_keys.constants import BillingModeChoice
from api_keys.models import UserProviderAPIKey


class SystemRefillPolicy(TimeStampMixin):
    """
    Singleton holding the platform-wide default refill amount and period.
    Edited via Django admin; seeded with $5 / 30 days to preserve existing behaviour.
    """
    refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal(DEFAULT_REFILL_AMOUNT),
        verbose_name=_("Default Refill Amount (USD)"),
        help_text=_("Platform-wide default refill amount applied to every user whose group/override does not specify one."),
    )
    refill_period_days = models.PositiveIntegerField(
        default=DEFAULT_REFILL_PERIOD_DAYS,
        verbose_name=_("Default Refill Period (days)"),
        help_text=_("Platform-wide default number of days between automatic refills."),
    )

    class Meta:
        verbose_name = _("System Refill Policy")
        verbose_name_plural = _("System Refill Policy")

    def clean(self):
        if self.refill_amount is not None and self.refill_amount < 0:
            raise ValidationError({"refill_amount": _("Refill amount cannot be negative.")})
        if self.refill_period_days is not None and self.refill_period_days < 1:
            raise ValidationError({"refill_period_days": _("Refill period must be at least 1 day.")})

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Return the singleton, creating it with defaults if absent."""
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"System refill: ${self.refill_amount} every {self.refill_period_days} day(s)"


class GroupWallet(TimeStampMixin):
    """
    Per-group wallet holding the budget a group owner manages, plus optional
    group-level overrides of refill amount / period. Member refills debit
    `budget_balance`; when it hits zero, refills pause until refunded.
    """
    group = models.OneToOneField(
        AccessCodeGroup,
        on_delete=models.CASCADE,
        related_name="group_wallet",
        verbose_name=_("Access Code Group"),
        help_text=_("The access code group this wallet configuration belongs to."),
    )
    budget_balance = models.DecimalField(
        max_digits=15,
        decimal_places=6,
        default=Decimal("0.00"),
        verbose_name=_("Budget Balance (USD)"),
        help_text=_(
            "Budget assigned to this group. Drained by scheduled refills and one-off "
            "allocations to members. Refills pause when this reaches zero."
        ),
    )
    refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("Group Refill Amount (USD)"),
        help_text=_("Per-member refill amount for this group. Null means inherit the system default."),
    )
    refill_period_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Group Refill Period (days)"),
        help_text=_("Days between automatic refills for members of this group. Null means inherit the system default."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
        help_text=_("When inactive, scheduled refills are paused for all members of this group."),
    )

    class Meta:
        verbose_name = _("Group Wallet")
        verbose_name_plural = _("Group Wallets")

    def clean(self):
        if self.refill_amount is not None and self.refill_amount < 0:
            raise ValidationError({"refill_amount": _("Refill amount cannot be negative.")})
        if self.refill_period_days is not None and self.refill_period_days < 1:
            raise ValidationError({"refill_period_days": _("Refill period must be at least 1 day.")})
        if self.budget_balance is not None and self.budget_balance < 0:
            raise ValidationError({"budget_balance": _("Budget balance cannot be negative.")})

    @property
    def display_budget(self):
        return f"${self.budget_balance:.2f}" if self.budget_balance is not None else "$0.00"

    def __str__(self):
        return f"GroupWallet<{self.group.access_code}> budget={self.display_budget}"


class UserRefillOverride(TimeStampMixin):
    """
    Per-user override of refill amount and/or period. Either field may be null
    to fall through to the group's value (or the system default). Set by admins
    platform-wide or by group owners for members of their own group.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="refill_override",
        verbose_name=_("User"),
        help_text=_("The user whose refill policy is being overridden."),
    )
    refill_amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("Refill Amount (USD)"),
        help_text=_("Custom refill amount for this user. Null means inherit from group/system."),
    )
    refill_period_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Refill Period (days)"),
        help_text=_("Custom period between refills for this user. Null means inherit from group/system."),
    )
    reason = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Reason"),
        help_text=_("Audit note explaining why this override exists."),
    )
    set_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refill_overrides_set",
        verbose_name=_("Set By"),
        help_text=_("Admin or group owner who created or last updated this override."),
    )

    class Meta:
        verbose_name = _("User Refill Override")
        verbose_name_plural = _("User Refill Overrides")

    def clean(self):
        if self.refill_amount is not None and self.refill_amount < 0:
            raise ValidationError({"refill_amount": _("Refill amount cannot be negative.")})
        if self.refill_period_days is not None and self.refill_period_days < 1:
            raise ValidationError({"refill_period_days": _("Refill period must be at least 1 day.")})

    def __str__(self):
        parts = []
        if self.refill_amount is not None:
            parts.append(f"${self.refill_amount}")
        if self.refill_period_days is not None:
            parts.append(f"{self.refill_period_days}d")
        detail = "/".join(parts) if parts else "inherit"
        return f"Override<{self.user.email}: {detail}>"


class Wallet(TimeStampMixin):
    """
    Model for user wallets.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="wallet",
        verbose_name=("User"),
        help_text=("The user associated with this wallet"),
    )
    balance = models.DecimalField(
        max_digits=15,
        decimal_places=6,
        default=Decimal("5.00"),
        verbose_name=("Balance"),
        help_text=("Wallet balance in USD"),
    )
    last_refill_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Refill At"),
        help_text=_("Timestamp of the most recent scheduled refill for this user. "
                    "Used by the scheduler to determine when the next refill is due."),
    )

    class Meta:
        verbose_name = ("Wallet")
        verbose_name_plural = ("Wallets")

    @property
    def display_balance(self):
        """
        Returns the balance formatted as USD.
        """
        return f"${self.balance:.2f}" if self.balance else ("No balance")

    def __str__(self):
        """
        Returns a string representation of the wallet.
        """
        return f"Wallet of {self.user.email} with balance {self.display_balance}"

class Transaction(TimeStampMixin):
    """
    Model for transactions in the wallet.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name=("User"),
        help_text=("The user associated with this transaction"),
    )
    message = models.TextField(
        blank=True,
        verbose_name=("Message"),
        help_text=("Description of the transaction"),
    )

    llm = models.ForeignKey(
        LLM,
        on_delete=models.SET_NULL,
        related_name="transactions",
        verbose_name=("Model"),
        help_text=("Model used in the transaction"),
        null=True,
        blank=True,
    )
    llm_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name=("Model Name"),
        help_text=("Name of the LLM model used (stored for historical reference)"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        verbose_name=("Amount"),
        help_text=("Transaction amount in USD"),
    )
    type = models.IntegerField(
        choices=TransactionTypeChoice.choices,
        verbose_name=("Transaction Type"),
        help_text=("Type of the transaction: debit or credit"),
    )
    source = models.CharField(
        max_length=30,
        choices=TransactionSourceChoice.choices,
        default=TransactionSourceChoice.OTHER,
        verbose_name=_("Source"),
        help_text=_("Origin of this transaction for reporting and auditing."),
    )
    related_group = models.ForeignKey(
        AccessCodeGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name=_("Related Group"),
        help_text=_("Access code group this transaction is associated with, if any."),
    )
    related_transaction = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="related_from",
        verbose_name=_("Related Transaction"),
        help_text=_("Paired transaction — for example, the informational owner row linked to a member's allocation credit."),
    )
    input_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=0,
        verbose_name=("Input Tokens"),
        help_text=("Number of input tokens used in the transaction"),
    )
    output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=0,
        verbose_name=("Output Tokens"),
        help_text=("Number of output tokens used in the transaction"),
    )
    billing_mode = models.CharField(
        max_length=20,
        choices=BillingModeChoice.choices,
        default=BillingModeChoice.WALLET,
        verbose_name=("Billing Mode"),
        help_text=("Billing mode used for this transaction: wallet or own API keys"),
    )
    platform = models.CharField(
        max_length=50,
        choices=AuthSourceChoice.choices,
        default=AuthSourceChoice.DARE,
        verbose_name=("Platform"),
        help_text=("Platform where this transaction originated: DARE or SocraticBots"),
    )

    # Energy/environmental impact tracking
    energy_wh = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=("Energy (Wh)"),
        help_text=("Estimated energy consumption in Watt-hours"),
    )
    carbon_g = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=("Carbon (g CO2e)"),
        help_text=("Estimated carbon emissions in grams CO2 equivalent"),
    )
    water_ml = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=("Water (mL)"),
        help_text=("Estimated water usage in milliliters"),
    )

    class Meta:
        verbose_name = ("Transaction")
        verbose_name_plural = ("Transactions")

    @property
    def display_amount(self):
        if self.amount is None:
            return "No amount"
        if self.amount == Decimal('0'):
            return "$0.00"
        if abs(self.amount) >= Decimal('0.01'):
            return f"${self.amount:.2f}"
        else:
            if abs(self.amount) < Decimal('0.0000001'):
                return f"${self.amount:.8e}"
            else:
                normalized = self.amount.normalize()
                return f"${normalized}"

    def save(self, *args, **kwargs):
        """
        Override save method to handle balance deduction for debit transactions.

        Platform-specific behavior:
        - DARE transactions: Deduct from/add to user's wallet balance
        - SocraticBots transactions: Record only (no wallet impact)

        Wallet-mode-specific behavior:
        - WALLET: debit/credit the user's DARE wallet (existing behaviour).
        - OWN_API (BYO) or LITELLM: record the transaction for analytics only;
          the cost is paid externally so the DARE wallet balance is untouched.
        """
        is_new = self.pk is None

        if is_new:
            if self.llm and not self.llm_name:
                self.llm_name = self.llm.name

            external_billing = self.billing_mode in (
                BillingModeChoice.OWN_API,
                BillingModeChoice.LITELLM,
            )

            # Only modify wallet balance for DARE platform transactions paid
            # from the user's DARE wallet (i.e. NOT external-billing modes).
            if self.platform == AuthSourceChoice.DARE and not external_billing:
                try:
                    wallet = self.user.wallet
                except self.user.wallet.RelatedObjectDoesNotExist:
                    wallet = Wallet.objects.create(user=self.user, balance=Decimal('5.00'))

                if self.type == TransactionTypeChoice.DEBIT:
                    if wallet.balance < self.amount:
                        raise ValidationError({
                            'error': ['insufficient_balance'],
                            'message': ['Insufficient wallet balance'],
                            'current_balance': [str(wallet.balance)],
                            'required_amount': [str(self.amount)]
                        })
                    wallet.balance -= self.amount
                elif self.type == TransactionTypeChoice.CREDIT:
                    wallet.balance += self.amount

                wallet.save(update_fields=['balance'])
            # SocraticBots / external-billing transactions are recorded but
            # don't affect the DARE wallet balance.

        super().save(*args, **kwargs)

    def __str__(self):
        """
        Returns a string representation of the transaction.
        """
        token_info = f", {self.input_tokens} input, {self.output_tokens} output tokens" if self.input_tokens is not None and self.output_tokens is not None else ""
        model_info = f" ({self.llm_name})" if self.llm_name else ""
        return f"{self.user.email}: {self.get_type_display()} - {self.display_amount}{model_info}{token_info}"


class BYOKeyFeatureFlag(models.Model):
    """
    Singleton flag gating the BYO Key wallet type globally. Edited by Django
    superadmin only; mirrors the SystemRefillPolicy singleton pattern.
    """
    SINGLETON_PK = 1

    is_enabled = models.BooleanField(
        default=False,
        verbose_name=_("BYO Key Enabled"),
        help_text=_("When enabled, users may add and select their own provider API keys (BYO) as the active wallet."),
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("Updated By"),
        help_text=_("Last admin to toggle this flag."),
    )

    class Meta:
        verbose_name = _("BYO Key Feature Flag")
        verbose_name_plural = _("BYO Key Feature Flag")

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def is_byo_enabled(cls) -> bool:
        row = cls.objects.filter(pk=cls.SINGLETON_PK).first()
        return bool(row and row.is_enabled)

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj

    def __str__(self):
        return f"BYO Key Feature Flag: {'ENABLED' if self.is_enabled else 'DISABLED'}"


class LiteLLMKey(TimeStampMixin):
    """
    Credential row representing a LiteLLM proxy key the user can route LLM
    calls through. Sourced either by the user themselves (`USER`) or issued
    by an admin to a single user (`ADMIN_USER`) or to an entire AccessCodeGroup
    (`ADMIN_GROUP`). Group keys are gated by AccessCodeGroup membership at
    query time — leaving the group hides the key implicitly.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(
        max_length=128,
        verbose_name=_("Label"),
        help_text=_("Human-readable name to disambiguate keys (e.g. 'Personal' or 'PHIL 101 - Spring 2026')."),
    )
    base_url = models.URLField(
        verbose_name=_("Base URL"),
        help_text=_("LiteLLM proxy URL — e.g. https://litellm-proxy.example.com."),
    )
    api_key = EncryptedCharField(
        max_length=500,
        verbose_name=_("API Key"),
        help_text=_("LiteLLM proxy API key (stored encrypted using AES-256)."),
    )
    source = models.CharField(
        max_length=16,
        choices=LiteLLMKeySourceChoice.choices,
        verbose_name=_("Source"),
        help_text=_("Where the key originated — user self-served, admin-issued to a user, or admin-issued to a group."),
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="litellm_keys_owned",
        verbose_name=_("Owner User"),
        help_text=_("Set when source=USER. The user who self-served this key."),
    )
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="litellm_keys_assigned",
        verbose_name=_("Assigned User"),
        help_text=_("Set when source=ADMIN_USER. The user the admin assigned this key to."),
    )
    source_group = models.ForeignKey(
        AccessCodeGroup,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="litellm_keys",
        verbose_name=_("Source Group"),
        help_text=_("Set when source=ADMIN_GROUP. The cohort whose members all have access to this key."),
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Expires At"),
        help_text=_("Optional hard expiry. Past expiry hides the key from the user's wallet list."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("Created By"),
        help_text=_("User who created this record (admin or self)."),
    )

    class Meta:
        verbose_name = _("LiteLLM Key")
        verbose_name_plural = _("LiteLLM Keys")
        constraints = [
            models.CheckConstraint(
                name="litellm_source_owner_consistency",
                condition=(
                    (Q(source=LiteLLMKeySourceChoice.USER)
                        & Q(owner_user__isnull=False)
                        & Q(assigned_user__isnull=True)
                        & Q(source_group__isnull=True))
                    | (Q(source=LiteLLMKeySourceChoice.ADMIN_USER)
                        & Q(assigned_user__isnull=False)
                        & Q(owner_user__isnull=True)
                        & Q(source_group__isnull=True))
                    | (Q(source=LiteLLMKeySourceChoice.ADMIN_GROUP)
                        & Q(source_group__isnull=False)
                        & Q(owner_user__isnull=True)
                        & Q(assigned_user__isnull=True))
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["owner_user"]),
            models.Index(fields=["assigned_user"]),
            models.Index(fields=["source_group"]),
        ]

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @classmethod
    def visible_for_user(cls, user):
        """
        Queryset of non-expired keys the given user has access to:
        their own self-served keys, admin-assigned individual keys, and
        ADMIN_GROUP keys whose source_group matches the user's current
        access_code_group (FK on User; not an M2M).
        """
        now = timezone.now()
        not_expired = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        user_group_id = getattr(user, "access_code_group_id", None)

        owned_or_assigned = (
            Q(source=LiteLLMKeySourceChoice.USER, owner_user=user)
            | Q(source=LiteLLMKeySourceChoice.ADMIN_USER, assigned_user=user)
        )
        if user_group_id:
            owned_or_assigned = owned_or_assigned | Q(
                source=LiteLLMKeySourceChoice.ADMIN_GROUP,
                source_group_id=user_group_id,
            )

        return cls.objects.filter(not_expired).filter(owned_or_assigned).select_related("source_group").distinct()

    def __str__(self):
        return f"LiteLLMKey<{self.label} / {self.get_source_display()}>"


class UserWalletPreference(TimeStampMixin):
    """
    Per-user pointer to the active wallet for routing LLM calls. Created lazily
    on first read with default DARE so existing users keep the current behaviour
    with no data migration. The router (`billing.wallet_router`) consults this
    on every call; admin-side changes self-heal here on the next request.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet_preference",
        verbose_name=_("User"),
    )
    active_wallet_type = models.CharField(
        max_length=16,
        choices=UserWalletPreferenceTypeChoice.choices,
        default=UserWalletPreferenceTypeChoice.DARE,
        verbose_name=_("Active Wallet Type"),
    )
    active_wallet_ref_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name=_("Active Wallet Reference"),
        help_text=_(
            "Identifier of the active wallet within its type table — "
            "UserProviderAPIKey.id (BYO) or LiteLLMKey.id (LITELLM). "
            "Null for DARE."
        ),
    )

    class Meta:
        verbose_name = _("User Wallet Preference")
        verbose_name_plural = _("User Wallet Preferences")

    def clean(self):
        wallet_type = self.active_wallet_type
        ref_id = self.active_wallet_ref_id

        if wallet_type == UserWalletPreferenceTypeChoice.DARE:
            if ref_id not in (None, ""):
                raise ValidationError({
                    "active_wallet_ref_id": _("DARE wallet must not have a ref_id."),
                })
            return

        if wallet_type == UserWalletPreferenceTypeChoice.BYO:
            if not BYOKeyFeatureFlag.is_byo_enabled():
                raise ValidationError({
                    "active_wallet_type": _("BYO wallet type is currently disabled platform-wide."),
                })

            # Collective BYO: ref_id is None ⇒ "use whichever BYO key matches
            # the requested provider at dispatch time." Validate that the user
            # has at least one populated BYO key configured.
            if not ref_id:
                has_any_byo = (
                    UserProviderAPIKey.active_objects
                    .filter(user=self.user)
                    .exclude(api_key__isnull=True)
                    .exclude(api_key="")
                    .exists()
                )
                if not has_any_byo:
                    raise ValidationError({
                        "active_wallet_ref_id": _(
                            "Add at least one BYO provider key before setting BYO active."
                        ),
                    })
                return

            # Legacy specific-key BYO mode.
            try:
                ref_pk = int(ref_id)
            except (TypeError, ValueError):
                raise ValidationError({"active_wallet_ref_id": _("BYO ref_id must be an integer pk.")})
            byo_qs = UserProviderAPIKey.active_objects.filter(pk=ref_pk, user=self.user)
            byo_row = byo_qs.first()
            if byo_row is None:
                raise ValidationError({
                    "active_wallet_ref_id": _("BYO key not found for this user."),
                })
            if not byo_row.has_key:
                raise ValidationError({
                    "active_wallet_ref_id": _("BYO key is empty — add a key value before setting it active."),
                })
            return

        if wallet_type == UserWalletPreferenceTypeChoice.LITELLM:
            if not ref_id:
                raise ValidationError({"active_wallet_ref_id": _("LiteLLM wallet requires a ref_id.")})
            if not LiteLLMKey.visible_for_user(self.user).filter(pk=ref_id).exists():
                raise ValidationError({
                    "active_wallet_ref_id": _("LiteLLM key not visible to this user (expired, missing, or unauthorized)."),
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_for(cls, user):
        pref, _created = cls.objects.get_or_create(
            user=user,
            defaults={
                "active_wallet_type": UserWalletPreferenceTypeChoice.DARE,
                "active_wallet_ref_id": None,
            },
        )
        return pref

    def reset_to_dare(self, save: bool = True):
        self.active_wallet_type = UserWalletPreferenceTypeChoice.DARE
        self.active_wallet_ref_id = None
        if save:
            self.save(update_fields=["active_wallet_type", "active_wallet_ref_id", "updated_at"])

    def __str__(self):
        ref = f":{self.active_wallet_ref_id}" if self.active_wallet_ref_id else ""
        return f"WalletPref<{self.user.email} -> {self.active_wallet_type}{ref}>"
