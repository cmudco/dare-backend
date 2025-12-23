from rest_framework import serializers
from django.utils import timezone

from notifications.models import Notification, UserNotificationReadStatus
from notifications.constants import NotificationStatus, NotificationDeliveryType, NotificationCategory


class NotificationListSerializer(serializers.ModelSerializer):
    """
    Serializer for listing notifications with essential fields
    """
    is_expired = serializers.ReadOnlyField()
    effective_status = serializers.SerializerMethodField()
    effective_read_at = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id',
            'title',
            'message',
            'delivery_type',
            'category',
            'status',
            'effective_status',
            'action_type',
            'action_url',
            'is_banner_notification',
            'is_expired',
            'created_at',
            'read_at',
            'effective_read_at',
        ]

    def get_effective_status(self, obj):
        """Get the effective status for the current user"""
        request = self.context.get('request')
        if request and request.user:
            return obj.get_status_for_user(request.user)
        return obj.status

    def get_effective_read_at(self, obj):
        """Get the effective read_at timestamp for the current user"""
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            if obj.user and obj.user == user:
                return obj.read_at
            elif obj.user is None:
                try:
                    user_read_status = UserNotificationReadStatus.objects.get(
                        user=user, notification=obj
                    )
                    return user_read_status.read_at
                except UserNotificationReadStatus.DoesNotExist:
                    return None
        return obj.read_at


class NotificationDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for detailed notification view
    """
    is_banner_notification = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    user_email = serializers.EmailField(source='user.email', read_only=True)
    effective_status = serializers.SerializerMethodField()
    effective_read_at = serializers.SerializerMethodField()

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
            'effective_status',
            'action_type',
            'action_url',
            'expires_at',
            'is_banner_notification',
            'is_expired',
            'created_at',
            'updated_at',
            'read_at',
            'effective_read_at',
        ]

    def get_effective_status(self, obj):
        """Get the effective status for the current user"""
        request = self.context.get('request')
        if request and request.user:
            return obj.get_status_for_user(request.user)
        return obj.status

    def get_effective_read_at(self, obj):
        """Get the effective read_at timestamp for the current user"""
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            if obj.user and obj.user == user:
                return obj.read_at
            elif obj.user is None:
                try:
                    user_read_status = UserNotificationReadStatus.objects.get(
                        user=user, notification=obj
                    )
                    return user_read_status.read_at
                except UserNotificationReadStatus.DoesNotExist:
                    return None
        return obj.read_at


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
            'source',  # Target platform (DARE or SocraticBots)
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
        Update notification using user-specific methods for proper global notification handling
        """
        new_status = validated_data.get('status')
        request = self.context.get('request')
        
        if request and request.user:
            user = request.user
            
            if new_status == NotificationStatus.READ:
                instance.mark_as_read_for_user(user)
            elif new_status == NotificationStatus.UNREAD:
                instance.mark_as_unread_for_user(user)
            elif new_status == NotificationStatus.ARCHIVED:
                instance.archive_for_user(user)
        
        # Return the instance (it will be re-serialized with current user context)
        return instance
