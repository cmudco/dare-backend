from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import IsDeletedMixin, TimeStampMixin
from core.config.processing import CHUNK_SIZE, OVERLAP_SIZE
from core.storage.constants import StorageBackendChoice
from users.managers import UserManager
from users.constants import VectorDBChoice, AuthSourceChoice, RoleChoice
from prompts.models import Prompt
from api_keys.constants import BillingModeChoice

class AccessCodeGroup(TimeStampMixin):
    """
    Represents a group of access codes for user registration.
    Tracks the total capacity and usage of registration codes.
    """
    max_capacity = models.IntegerField(
        help_text="Maximum number of users that can use this access code group"
    )
    current_usage = models.IntegerField(
        default=0,
        help_text="Number of times this access code has been used"
    )
    access_code = models.CharField(
        max_length=255,
        unique=True,
        help_text="Unique registration code for this group"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this access code is currently active"
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Expiration Date"),
        help_text=_("Date and time when this access code expires. Users with this code will be deactivated after this date. Leave blank for no expiration.")
    )
    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Notes"),
        help_text=_("Internal notes about this access code group (e.g., class name, semester, purpose)")
    )
    # DEPRECATED: Platform access is now determined by default_role
    # This field is kept for backwards compatibility and will be removed in a future migration
    scope = models.CharField(
        max_length=50,
        choices=[("DARE", "DARE Only"), ("DUAL", "DARE + SocraticBots")],
        default="DARE",
        verbose_name=_("Access Scope (Deprecated)"),
        help_text=_("DEPRECATED: Use default_role instead. Platform access is now role-based.")
    )
    # Link this access code group to a model group to control available LLMs
    model_group = models.ForeignKey(
        'conversations.ModelGroup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='access_code_groups',
        help_text=_("Model group applied to users who register with this access code group")
    )
    # Optional: set an initial wallet credit for users who register with this code
    initial_wallet_credit = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text=_("Optional initial wallet credit (USD) to grant new users who register with this access code. If left blank, normal defaults apply."),
        verbose_name=_("Initial Wallet Credit (USD)")
    )
    # Default role assigned to users who register with this access code
    default_role = models.CharField(
        max_length=20,
        choices=RoleChoice.choices,
        default=RoleChoice.USER,
        verbose_name=_("Default Role"),
        help_text=_("Role assigned to users who register with this access code. "
                    "SUPERVISOR: DARE platform access + cross-user bot/agent management in SocraticBooks + creator access. "
                    "RESEARCHER: DARE platform access + SocraticBooks creator (can create/manage books). "
                    "USER: DARE platform access + SocraticBooks student/consumer (can read/interact with books). "
                    "CREATOR: No DARE access + SocraticBooks creator (can create/manage books). "
                    "SB_USER: No DARE access + SocraticBooks student/consumer only.")
    )

    class Meta:
        verbose_name = "Access Code Group"
        verbose_name_plural = "Access Code Groups"

    def __str__(self):
        role_indicator = f" [{self.get_default_role_display()}]"
        expiration_indicator = ""
        if self.expires_at:
            if self.is_expired:
                expiration_indicator = " [EXPIRED]"
            else:
                expiration_indicator = f" [Expires: {self.expires_at.strftime('%Y-%m-%d')}]"
        return f"Access Code: {self.access_code} ({self.current_usage}/{self.max_capacity} used){role_indicator}{expiration_indicator}"

    @property
    def is_expired(self):
        """Check if the access code has expired"""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    @property
    def is_available(self):
        """Check if the access code can still be used"""
        return self.is_active and self.current_usage < self.max_capacity and not self.is_expired

    def use_code(self):
        """Increment usage count when code is used"""
        if self.is_available:
            self.current_usage += 1
            self.save(update_fields=['current_usage'])
            return True
        return False

    def deactivate_all_users(self):
        """Deactivate all users associated with this access code group"""
        return self.users.update(is_active=False)

    def reactivate_all_users(self):
        """Reactivate all users associated with this access code group"""
        return self.users.update(is_active=True)

class User(AbstractUser, IsDeletedMixin):
    username = None
    email = models.EmailField(_("email"), unique=True, blank=False, null=False)
    REQUIRED_FIELDS = []
    USERNAME_FIELD = "email"

    country = models.CharField(max_length=100, blank=True, null=True)
    access_code_group = models.ForeignKey(
        AccessCodeGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        help_text=_("Access code group this user belongs to")
    )
    vector_db = models.IntegerField(
        choices=VectorDBChoice.choices,
        default=VectorDBChoice.WEAVIATE,
        verbose_name=_("Vector Database"),
        help_text=_("Vector database to use for this user's data")
    )
    storage_backend = models.IntegerField(
        choices=StorageBackendChoice.choices,
        default=StorageBackendChoice.LOCAL,
        verbose_name=_("Storage Backend"),
        help_text=_("Preferred storage backend for new file uploads")
    )
    default_prompt = models.ForeignKey(
        Prompt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_users",
        verbose_name=_("Default Prompt"),
        help_text=_("The default prompt for this user, if set.")
    )
    chunk_size = models.IntegerField(
        default=CHUNK_SIZE,
        verbose_name=_("Chunk Size"),
        help_text=_("Size of text chunks for document processing.")
    )
    overlap_size = models.IntegerField(
        default=OVERLAP_SIZE,
        verbose_name=_("Overlap Size"),
        help_text=_("Size of overlap between text chunks")
    )
    # Additional fields sourced from onboarding form
    role = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Role/Profession/Student"),
        help_text=_("User's role, profession, or student status")
    )
    industry = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Industry/Major"),
        help_text=_("User's industry, domain of study, or academic major")
    )
    purpose = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Goals of using DARE"),
        help_text=_("User's goals for using DARE")
    )
    referral_source = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Referral Source"),
        help_text=_("Where did you hear about DARE?/What class were you assigned access to DARE?")
    )
    is_onboarding_completed = models.BooleanField(
        default=False,
        verbose_name=_("Onboarding Completed"),
        help_text=_("Whether the user has completed the onboarding process")
    )

    # Platform-specific authentication fields
    auth_source = models.CharField(
        max_length=50,
        choices=AuthSourceChoice.choices,
        default=AuthSourceChoice.DARE,
        verbose_name=_("Authentication Source"),
        help_text=_("Platform where the user was originally authenticated")
    )
    is_dare_accessible = models.BooleanField(
        default=True,
        verbose_name=_("DARE Access"),
        help_text=_("Whether this user can access DARE platform")
    )
    is_socratic_bots_accessible = models.BooleanField(
        default=False,
        verbose_name=_("SocraticBots Access"),
        help_text=_("Whether this user can access SocraticBots platform")
    )

    # Platform role - determines user's permissions across DARE and SocraticBots
    platform_role = models.CharField(
        max_length=20,
        choices=RoleChoice.choices,
        default=RoleChoice.USER,
        verbose_name=_("Platform Role"),
        help_text=_("User's role across DARE and SocraticBots platforms. "
                    "ADMIN: DARE admin access + SB creator + voice agent. "
                    "RESEARCHER: DARE access + SB creator. "
                    "USER: DARE access + SB student/consumer. "
                    "CREATOR: No DARE + SB creator. "
                    "SB_USER: No DARE + SB student/consumer only.")
    )

    # Billing mode - determines how user pays for API usage
    billing_mode = models.CharField(
        max_length=20,
        choices=BillingModeChoice.choices,
        default=BillingModeChoice.WALLET,
        verbose_name=_("Billing Mode"),
        help_text=_("How the user pays for API usage: wallet credits or own API keys")
    )

    # Avatar settings
    avatar_type = models.CharField(
        max_length=20,
        choices=[('initials', 'Initials'), ('preset', 'Preset Avatar'), ('custom', 'Custom Upload')],
        default='initials',
        verbose_name=_("Avatar Type"),
        help_text=_("Type of avatar: initials, preset image, or custom upload")
    )
    avatar_preset = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name=_("Avatar Preset"),
        help_text=_("Identifier for preset avatar image")
    )
    avatar_url = models.URLField(
        blank=True,
        null=True,
        verbose_name=_("Avatar URL"),
        help_text=_("URL for custom uploaded avatar image")
    )

    objects = UserManager()
    active_objects = ActiveObjectsManager()

    @property
    def full_name(self):
        """
        Returns the user's full name.
        """
        return self.get_full_name()

    def disable(self):
        """
        Disables (sets `is_active` to `False`) the current instance of the model.
        """
        self.is_active = False
        self.save(update_fields=["is_active"])

    def enable(self):
        """
        Enables (sets `is_active` to `True`) the current instance of the model.
        """
        self.is_active = True
        self.save(update_fields=["is_active"])
