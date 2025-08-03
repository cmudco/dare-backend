from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from common.models import BaseModel
from common.managers import ActiveObjectsManager
from .constants import NotificationDeliveryType, NotificationCategory, NotificationStatus, NotificationAction


class Notification(BaseModel):
    """
    Notification model supporting both system and user-specific notifications.
    When user is null, it's a system-wide notification.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        help_text=_("User who will receive this notification. Null for system notifications.")
    )

    title = models.CharField(
        max_length=255,
        help_text=_("Title of the notification")
    )

    message = models.TextField(
        help_text=_("Main content of the notification")
    )

    delivery_type = models.CharField(
        max_length=10,
        choices=NotificationDeliveryType.choices,
        default=NotificationDeliveryType.PANEL,
        help_text=_("How the notification should be delivered (panel or banner)")
    )

    category = models.CharField(
        max_length=15,
        choices=NotificationCategory.choices,
        default=NotificationCategory.DEFAULT,
        help_text=_("Visual category that maps to toast variants")
    )

    status = models.CharField(
        max_length=10,
        choices=NotificationStatus.choices,
        default=NotificationStatus.UNREAD,
        help_text=_("Current status of the notification")
    )

    action_type = models.CharField(
        max_length=15,
        choices=NotificationAction.choices,
        default=NotificationAction.NONE,
        help_text=_("Action that can be performed on this notification")
    )

    action_url = models.URLField(
        blank=True,
        null=True,
        help_text=_("URL to navigate to when notification is clicked")
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this notification expires (optional)")
    )

    read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the notification was marked as read")
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['delivery_type']),
            models.Index(fields=['category']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        user_info = f"User: {self.user.email}" if self.user else "System"
        return f"{self.title} ({user_info}) - {self.get_status_display()}"

    @property
    def is_banner_notification(self):
        """Check if this notification should be displayed as a banner"""
        return self.delivery_type == NotificationDeliveryType.BANNER

    @property
    def is_expired(self):
        """Check if the notification has expired"""
        if not self.expires_at:
            return False
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def mark_as_read(self):
        """Mark notification as read"""
        from django.utils import timezone
        self.status = NotificationStatus.READ
        self.read_at = timezone.now()
        self.save(update_fields=['status', 'read_at'])

    def mark_as_unread(self):
        """Mark notification as unread"""
        self.status = NotificationStatus.UNREAD
        self.read_at = None
        self.save(update_fields=['status', 'read_at'])

    def archive(self):
        """Archive the notification"""
        self.status = NotificationStatus.ARCHIVED
        self.save(update_fields=['status'])

    def get_status_for_user(self, user):
        """Get the effective status of this notification for a specific user"""
        if self.user and self.user == user:
            # User-specific notification - use the notification's own status
            return self.status
        elif self.user is None:
            # Global notification - check user's read status
            try:
                user_read_status = UserNotificationReadStatus.objects.get(
                    user=user, notification=self
                )
                return user_read_status.status
            except UserNotificationReadStatus.DoesNotExist:
                # User hasn't interacted with this notification yet
                return self.status
        else:
            # Notification belongs to another user
            return None

    def mark_as_read_for_user(self, user):
        """Mark notification as read for a specific user"""
        if self.user and self.user == user:
            # User-specific notification - update the notification directly
            self.mark_as_read()
        elif self.user is None:
            # Global notification - create/update user read status
            user_read_status, created = UserNotificationReadStatus.objects.get_or_create(
                user=user,
                notification=self,
                defaults={
                    'status': NotificationStatus.READ,
                    'read_at': timezone.now()
                }
            )
            if not created and user_read_status.status != NotificationStatus.READ:
                user_read_status.status = NotificationStatus.READ
                user_read_status.read_at = timezone.now()
                user_read_status.save(update_fields=['status', 'read_at'])

    def mark_as_unread_for_user(self, user):
        """Mark notification as unread for a specific user"""
        if self.user and self.user == user:
            # User-specific notification - update the notification directly
            self.mark_as_unread()
        elif self.user is None:
            # Global notification - create/update user read status
            user_read_status, created = UserNotificationReadStatus.objects.get_or_create(
                user=user,
                notification=self,
                defaults={
                    'status': NotificationStatus.UNREAD,
                    'read_at': None
                }
            )
            if not created:
                user_read_status.status = NotificationStatus.UNREAD
                user_read_status.read_at = None
                user_read_status.save(update_fields=['status', 'read_at'])

    def archive_for_user(self, user):
        """Archive notification for a specific user"""
        if self.user and self.user == user:
            # User-specific notification - update the notification directly
            self.archive()
        elif self.user is None:
            # Global notification - create/update user read status
            user_read_status, created = UserNotificationReadStatus.objects.get_or_create(
                user=user,
                notification=self,
                defaults={'status': NotificationStatus.ARCHIVED}
            )
            if not created:
                user_read_status.status = NotificationStatus.ARCHIVED
                user_read_status.save(update_fields=['status'])


class UserNotificationReadStatus(BaseModel):
    """
    Track user-specific read status for global notifications.
    This allows global notifications to have different read states per user.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_read_statuses',
        help_text=_("User who has this read status")
    )
    
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name='user_read_statuses',
        help_text=_("Notification this read status applies to")
    )
    
    status = models.CharField(
        max_length=10,
        choices=NotificationStatus.choices,
        default=NotificationStatus.READ,
        help_text=_("User's status for this notification")
    )
    
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the user marked this notification as read")
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("User Notification Read Status")
        verbose_name_plural = _("User Notification Read Statuses")
        unique_together = ('user', 'notification')
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['notification', 'status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.notification.title} ({self.get_status_display()})"
