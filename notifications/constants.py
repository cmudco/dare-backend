from enum import Enum
from django.db import models

APP_NAME = "notifications"

class NotificationDeliveryType(models.TextChoices):
    """How the notification should be delivered"""
    PANEL = 'panel', 'Notification Panel'
    BANNER = 'banner', 'Site Banner'

class NotificationCategory(models.TextChoices):
    """Categories that map to toast variants"""
    DEFAULT = 'default', 'Default'
    DESTRUCTIVE = 'destructive', 'Error/Critical'
    SUCCESS = 'success', 'Success'
    WARNING = 'warning', 'Warning'
    INFO = 'info', 'Information'

class NotificationStatus(models.TextChoices):
    """Status of notifications"""
    UNREAD = 'unread', 'Unread'
    READ = 'read', 'Read'
    ARCHIVED = 'archived', 'Archived'

class NotificationAction(models.TextChoices):
    """Available actions for notifications"""
    NONE = 'none', 'None'
    NAVIGATE = 'navigate', 'Navigate'
    DISMISS = 'dismiss', 'Dismiss'
    ACKNOWLEDGE = 'acknowledge', 'Acknowledge'