from django.db import models


class ActiveObjectsManager(models.Manager):
    """Active Objects manager."""

    def get_queryset(self):
        """
        Return active objects of model.
        """
        return super().get_queryset().filter(is_active=True, is_deleted=False)


class IsAdminUserManager(models.Manager):
    """
    Admin Objects Manager.
    """

    def get_queryset(self):
        """
        Returns only those objects whose is_admin is True
        """
        return super().get_queryset().filter(is_admin=True)
