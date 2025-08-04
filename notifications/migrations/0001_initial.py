import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_active', models.BooleanField(default=True)),
                ('is_deleted', models.BooleanField(default=False)),
                ('title', models.CharField(help_text='Title of the notification', max_length=255)),
                ('message', models.TextField(help_text='Main content of the notification')),
                ('delivery_type', models.CharField(choices=[('panel', 'Notification Panel'), ('banner', 'Site Banner')], default='panel', help_text='How the notification should be delivered (panel or banner)', max_length=10)),
                ('category', models.CharField(choices=[('default', 'Default'), ('destructive', 'Error/Critical'), ('success', 'Success'), ('warning', 'Warning'), ('info', 'Information')], default='default', help_text='Visual category that maps to toast variants', max_length=15)),
                ('status', models.CharField(choices=[('unread', 'Unread'), ('read', 'Read'), ('archived', 'Archived')], default='unread', help_text='Current status of the notification', max_length=10)),
                ('action_type', models.CharField(choices=[('none', 'None'), ('navigate', 'Navigate'), ('dismiss', 'Dismiss'), ('acknowledge', 'Acknowledge')], default='none', help_text='Action that can be performed on this notification', max_length=15)),
                ('action_url', models.URLField(blank=True, help_text='URL to navigate to when notification is clicked', null=True)),
                ('metadata', models.JSONField(blank=True, default=dict, help_text='Additional metadata for the notification (e.g., conversation_id, file_id)')),
                ('expires_at', models.DateTimeField(blank=True, help_text='When this notification expires (optional)', null=True)),
                ('read_at', models.DateTimeField(blank=True, help_text='When the notification was marked as read', null=True)),
                ('user', models.ForeignKey(blank=True, help_text='User who will receive this notification. Null for system notifications.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Notification',
                'verbose_name_plural': 'Notifications',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['user', 'status'], name='notificatio_user_id_7088ed_idx'), models.Index(fields=['delivery_type'], name='notificatio_deliver_f9cca6_idx'), models.Index(fields=['category'], name='notificatio_categor_fd561f_idx'), models.Index(fields=['created_at'], name='notificatio_created_46ad24_idx')],
            },
        ),
    ]
