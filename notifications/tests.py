from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from notifications.constants import NotificationStatus
from notifications.models import Notification

User = get_user_model()


class NotificationHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="notification-owner@example.com",
            password="password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.unread = Notification.objects.create(
            user=self.user,
            title="Unread notification",
            message="Unread",
        )
        self.read = Notification.objects.create(
            user=self.user,
            title="Read notification",
            message="Read",
            status=NotificationStatus.READ,
        )
        self.archived = Notification.objects.create(
            user=self.user,
            title="Archived notification",
            message="Archived",
            status=NotificationStatus.ARCHIVED,
        )

    def test_default_list_only_returns_unread_notifications(self):
        response = self.client.get("/api/notifications/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [notification["id"] for notification in response.data["results"]],
            [self.unread.pk],
        )

    def test_include_read_returns_active_notification_history(self):
        response = self.client.get("/api/notifications/?include_read=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {notification["id"] for notification in response.data["results"]},
            {self.unread.pk, self.read.pk},
        )
