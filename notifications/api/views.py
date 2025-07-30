from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.pagination import CustomPageNumberPagination
from notifications.models import Notification
from .serializers import (
    NotificationListSerializer,
    NotificationDetailSerializer,
    NotificationCreateSerializer,
    NotificationUpdateSerializer,
)


class NotificationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing notifications - supports CRUD operations
    Users can see their own notifications + system notifications
    """
    pagination_class = CustomPageNumberPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Return notifications for the current user plus system notifications
        """
        user = self.request.user
        queryset = Notification.active_objects.filter(
            Q(user=user) | Q(user__isnull=True)
        )

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        delivery_type_filter = self.request.query_params.get('delivery_type')
        if delivery_type_filter:
            queryset = queryset.filter(delivery_type=delivery_type_filter)

        category_filter = self.request.query_params.get('category')
        if category_filter:
            queryset = queryset.filter(category=category_filter)

        exclude_expired = self.request.query_params.get('exclude_expired', 'true').lower() == 'true'
        if exclude_expired:
            queryset = queryset.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            )

        return queryset.order_by('-created_at')

    def get_serializer_class(self):
        """
        Return appropriate serializer class based on action
        """
        if self.action == 'create':
            return NotificationCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return NotificationUpdateSerializer
        elif self.action == 'retrieve':
            return NotificationDetailSerializer
        return NotificationListSerializer

    def perform_create(self, serializer):
        """
        Create notification - if no user specified, it becomes a system notification
        Only allow admins to create system notifications
        """
        if not serializer.validated_data.get('user') and not self.request.user.is_staff:
            serializer.validated_data['user'] = self.request.user

        serializer.save()

    @action(detail=False, methods=['get'], url_path='stats')
    def get_stats(self, request):
        """
        Get notification statistics for the current user
        """
        user = request.user

        user_notifications = Notification.active_objects.filter(
            Q(user=user) | Q(user__isnull=True)
        )

        active_notifications = user_notifications.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        )

        stats = {
            'total_notifications': active_notifications.count(),
            'unread_notifications': active_notifications.filter(status='unread', delivery_type='panel').count(),
            'read_notifications': active_notifications.filter(status='read').count(),
            'archived_notifications': active_notifications.filter(status='archived').count(),
            'system_notifications': active_notifications.filter(user__isnull=True).count(),
            'user_notifications': active_notifications.filter(user=user).count(),
        }

        delivery_type_counts = {}
        for delivery_type in active_notifications.values_list('delivery_type', flat=True).distinct():
            delivery_type_counts[delivery_type] = active_notifications.filter(delivery_type=delivery_type).count()
        stats['notifications_by_delivery_type'] = delivery_type_counts

        category_counts = {}
        for category in active_notifications.values_list('category', flat=True).distinct():
            category_counts[category] = active_notifications.filter(category=category).count()
        stats['notifications_by_category'] = category_counts

        return Response(stats)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_as_read(self, request):
        """
        Mark all unread notifications as read for the current user
        """
        user = request.user

        updated_count = Notification.active_objects.filter(
            Q(user=user) | Q(user__isnull=True),
            status='unread'
        ).update(
            status='read',
            read_at=timezone.now(),
            updated_at=timezone.now()
        )

        return Response({
            'message': f'Marked {updated_count} notifications as read',
            'updated_count': updated_count
        })

    @action(detail=False, methods=['delete'], url_path='clear-all')
    def clear_all_notifications(self, request):
        """
        Soft delete all notifications visible to the current user (including system notifications)
        """
        user = request.user

        updated_count = Notification.active_objects.filter(
            Q(user=user) | Q(user__isnull=True)
        ).update(is_deleted=True, updated_at=timezone.now())

        return Response({
            'message': f'Cleared {updated_count} notifications',
            'cleared_count': updated_count
        })
