import logging

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import IsDeletedMixin
from users.managers import UserManager

logger = logging.getLogger(__name__)


class User(AbstractUser, IsDeletedMixin):
    username = None
    email = models.EmailField(_("email"), unique=True, blank=False, null=False)
    REQUIRED_FIELDS = []
    USERNAME_FIELD = "email"

    country = models.CharField(max_length=100, blank=True, null=True)

    objects = UserManager()
    active_objects = ActiveObjectsManager()

    @property
    def full_name(self):
        """
        Returns True if the user has subscribed to any package other than the free one, otherwise False.
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
