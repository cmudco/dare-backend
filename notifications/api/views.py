from django.db.models import Q, Case, When, CharField, Value
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.pagination import CustomPageNumberPagination
from notifications.models import Notification, UserNotificationReadStatus
from notifications.constants import NotificationStatus
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
        with user-specific read status for global notifications
        """
        user = self.request.user
        
        # Base queryset: user's own notifications + global notifications
        queryset = Notification.active_objects.filter(
            Q(user=user) | Q(user__isnull=True)
        ).select_related('user').prefetch_related('user_read_statuses')
        
        # For filtering, we need to use a subquery approach since Case with joins can be unreliable
        from django.db.models import OuterRef, Subquery
        
        # Subquery to get user's read status for global notifications
        user_read_status_subquery = UserNotificationReadStatus.objects.filter(
            user=user,
            notification=OuterRef('pk')
        ).values('status')[:1]
        
        # Annotate with effective status for this user
        queryset = queryset.annotate(
            effective_status=Case(
                # For user-specific notifications, use the notification's status
                When(user=user, then='status'),
                # For global notifications, use user's read status if exists, otherwise notification's status
                When(
                    user__isnull=True,
                    then=Case(
                        When(
                            id__in=UserNotificationReadStatus.objects.filter(user=user).values('notification_id'),
                            then=Subquery(user_read_status_subquery)
                        ),
                        default='status',
                        output_field=CharField()
                    )
                ),
                default=Value('unread'),
                output_field=CharField()
            )
        )

        # Apply status filter using effective status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(effective_status=status_filter)

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

        # By default, exclude read notifications for all delivery types
        # This can be overridden by explicitly setting status=read in query params
        if not status_filter:
            queryset = queryset.exclude(effective_status=NotificationStatus.READ)

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
        Get notification statistics for the current user with user-specific read status
        """
        user = request.user

        # Get all notifications visible to user with effective status
        user_notifications = Notification.active_objects.filter(
            Q(user=user) | Q(user__isnull=True)
        ).select_related('user').prefetch_related('user_read_statuses')

        active_notifications = user_notifications.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        )

        # Calculate stats considering user-specific read status
        total_count = 0
        unread_count = 0
        read_count = 0
        archived_count = 0
        system_count = 0
        user_count = 0

        delivery_type_counts = {}
        category_counts = {}

        for notification in active_notifications:
            total_count += 1
            
            # Get effective status for this user
            effective_status = notification.get_status_for_user(user)
            
            if effective_status == NotificationStatus.UNREAD:
                unread_count += 1
                if notification.delivery_type != 'panel':
                    unread_count -= 1  # Only count panel notifications in unread count
            elif effective_status == NotificationStatus.READ:
                read_count += 1
            elif effective_status == NotificationStatus.ARCHIVED:
                archived_count += 1
            
            # Count system vs user notifications
            if notification.user is None:
                system_count += 1
            elif notification.user == user:
                user_count += 1
            
            # Count by delivery type
            delivery_type = notification.delivery_type
            delivery_type_counts[delivery_type] = delivery_type_counts.get(delivery_type, 0) + 1
            
            # Count by category
            category = notification.category
            category_counts[category] = category_counts.get(category, 0) + 1

        stats = {
            'total_notifications': total_count,
            'unread_notifications': unread_count,
            'read_notifications': read_count,
            'archived_notifications': archived_count,
            'system_notifications': system_count,
            'user_notifications': user_count,
            'notifications_by_delivery_type': delivery_type_counts,
            'notifications_by_category': category_counts,
        }

        return Response(stats)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_as_read(self, request):
        """
        Mark all unread notifications as read for the current user
        """
        user = request.user
        updated_count = 0

        # Get all notifications visible to this user
        notifications = Notification.active_objects.filter(
            Q(user=user) | Q(user__isnull=True)
        ).select_related('user').prefetch_related('user_read_statuses')

        for notification in notifications:
            effective_status = notification.get_status_for_user(user)
            if effective_status == NotificationStatus.UNREAD:
                notification.mark_as_read_for_user(user)
                updated_count += 1

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

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_notification_as_read(self, request, pk=None):
        """
        Mark a specific notification as read for the current user
        """
        user = request.user
        notification = self.get_object()
        
        # Check if user can access this notification
        if notification.user and notification.user != user:
            return Response(
                {'error': 'You do not have permission to access this notification'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        notification.mark_as_read_for_user(user)
        
        # Return updated notification data
        serializer = self.get_serializer(notification)
        return Response({
            'message': 'Notification marked as read',
            'notification': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='mark-unread')
    def mark_notification_as_unread(self, request, pk=None):
        """
        Mark a specific notification as unread for the current user
        """
        user = request.user
        notification = self.get_object()
        
        # Check if user can access this notification
        if notification.user and notification.user != user:
            return Response(
                {'error': 'You do not have permission to access this notification'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        notification.mark_as_unread_for_user(user)
        
        # Return updated notification data
        serializer = self.get_serializer(notification)
        return Response({
            'message': 'Notification marked as unread',
            'notification': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='archive')
    def archive_notification(self, request, pk=None):
        """
        Archive a specific notification for the current user
        """
        user = request.user
        notification = self.get_object()
        
        # Check if user can access this notification
        if notification.user and notification.user != user:
            return Response(
                {'error': 'You do not have permission to access this notification'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        notification.archive_for_user(user)
        
        # Return updated notification data
        serializer = self.get_serializer(notification)
        return Response({
            'message': 'Notification archived',
            'notification': serializer.data
        })
