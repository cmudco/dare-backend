from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from .models import Notification, UserNotificationReadStatus
from .constants import NotificationStatus, NotificationCategory


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        'title',
        'user_display',
        'source',
        'delivery_type',
        'category_display',
        'created_at',
        'expires_at'
    ]
    list_filter = [
        'source',
        'delivery_type',
        'category',
        'created_at'
    ]
    search_fields = [
        'title',
        'message',
        'user__email',
        'user__first_name',
        'user__last_name'
    ]
    readonly_fields = [
        'created_at',
        'read_at'
    ]
    fieldsets = (
        ('Basic Information', {
            'fields': ('user', 'title', 'message', 'source', 'delivery_type', 'category'),
            'description': 'Leave user empty to send notification to all users'
        }),
        ('Display Settings', {
            'fields': ('action_url',)
        }),
        ('Scheduling', {
            'fields': ('expires_at',),
            'classes': ('collapse',)
        })
    )

    def user_display(self, obj):
        """Display user information or All Users for global notifications"""
        if obj.user:
            return f"{obj.user.email}"
        return format_html('<strong style="color: #ff6b35;">All Users</strong>')
    user_display.short_description = 'Target'

    def category_display(self, obj):
        """Display category with color coding based on toast variants"""
        colors = {
            NotificationCategory.DEFAULT: '#6c757d',
            NotificationCategory.DESTRUCTIVE: '#dc3545',
            NotificationCategory.SUCCESS: '#28a745',
            NotificationCategory.WARNING: '#ffc107',
            NotificationCategory.INFO: '#007bff',
        }
        color = colors.get(obj.category, '#6c757d')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_category_display()
        )
    category_display.short_description = 'Category'

    def get_queryset(self, request):
        """Include all notifications in admin for management"""
        return super().get_queryset(request).select_related('user')


@admin.register(UserNotificationReadStatus)
class UserNotificationReadStatusAdmin(admin.ModelAdmin):
    list_display = [
        'user_email',
        'notification_title',
        'status',
        'read_at',
        'created_at'
    ]
    list_filter = [
        'status',
        'created_at',
        'read_at'
    ]
    search_fields = [
        'user__email',
        'notification__title',
        'notification__message'
    ]
    readonly_fields = [
        'created_at',
        'updated_at'
    ]

    def user_email(self, obj):
        """Display user email"""
        return obj.user.email
    user_email.short_description = 'User'

    def notification_title(self, obj):
        """Display notification title with link"""
        return format_html(
            '<a href="/admin/notifications/notification/{}/change/">{}</a>',
            obj.notification.id,
            obj.notification.title
        )
    notification_title.short_description = 'Notification'

    def get_queryset(self, request):
        """Optimize queries"""
        return super().get_queryset(request).select_related('user', 'notification')
