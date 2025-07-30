from rest_framework import serializers
from django.utils import timezone

from notifications.models import Notification
from notifications.constants import NotificationStatus, NotificationDeliveryType, NotificationCategory


class NotificationListSerializer(serializers.ModelSerializer):
    """
    Serializer for listing notifications with essential fields
    """
    is_expired = serializers.ReadOnlyField()

    class Meta:
        model = Notification
        fields = [
            'id',
            'title',
            'message',
            'delivery_type',
            'category',
            'status',
            'action_type',
            'action_url',
            'is_banner_notification',
            'is_expired',
            'created_at',
            'read_at',
        ]


class NotificationDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for detailed notification view
    """
    is_banner_notification = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'user_email',
            'title',
            'message',
            'delivery_type',
            'category',
            'status',
            'action_type',
            'action_url',
            'expires_at',
            'is_banner_notification',
            'is_expired',
            'created_at',
            'updated_at',
            'read_at',
        ]


class NotificationCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating notifications
    """

    class Meta:
        model = Notification
        fields = [
            'user',
            'title',
            'message',
            'delivery_type',
            'category',
            'action_type',
            'action_url',
            'expires_at',
        ]

    def validate(self, attrs):
        """
        Validate notification data
        """
        if attrs.get('action_type') == 'navigate' and not attrs.get('action_url'):
            raise serializers.ValidationError({
                'action_url': 'Action URL is required when action type is navigate.'
            })

        return attrs


class NotificationUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating notification status
    """

    class Meta:
        model = Notification
        fields = ['status']

    def update(self, instance, validated_data):
        """
        Update notification and set read_at timestamp if marking as read
        """
        new_status = validated_data.get('status')

        if new_status == NotificationStatus.READ and instance.status != NotificationStatus.READ:
            instance.read_at = timezone.now()
        elif new_status == NotificationStatus.UNREAD:
            instance.read_at = None

        instance.status = new_status
        instance.save(update_fields=['status', 'read_at'])
        return instance
