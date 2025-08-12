from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import IsDeletedMixin, TimeStampMixin
from core.config.processing import CHUNK_SIZE, OVERLAP_SIZE
from users.managers import UserManager
from users.constants import VectorDBChoice, AuthSourceChoice, ScopeChoice
from prompts.models import Prompt

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
    scope = models.CharField(
        max_length=50,
        choices=ScopeChoice.choices,
        default=ScopeChoice.DARE,
        verbose_name=_("Access Scope"),
        help_text=_("Determines which platforms users can access with this code")
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

    class Meta:
        verbose_name = "Access Code Group"
        verbose_name_plural = "Access Code Groups"

    def __str__(self):
        scope_indicator = f" [{self.get_scope_display()}]"
        return f"Access Code: {self.access_code} ({self.current_usage}/{self.max_capacity} used){scope_indicator}"

    @property
    def is_available(self):
        """Check if the access code can still be used"""
        return self.is_active and self.current_usage < self.max_capacity

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
