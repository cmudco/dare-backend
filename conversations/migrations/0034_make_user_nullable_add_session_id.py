# Generated migration for public bot support

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0033_backfill_bot_id_for_existing_conversations'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversation',
            name='anonymous_session_id',
            field=models.CharField(blank=True, db_index=True, help_text='Session ID for anonymous public bot conversations.', max_length=255, null=True),
        ),
        migrations.AlterField(
            model_name='conversation',
            name='user',
            field=models.ForeignKey(blank=True, help_text='User who owns this conversation. Null for public bot conversations.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='conversations', to=settings.AUTH_USER_MODEL),
        ),
    ]
