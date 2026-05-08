"""
Add ``Conversation.access_code`` so DARE's billing finalizer can resolve the
matching ``AccessCodeGroup`` (and its ``GroupWallet``) for institutional bots
without round-tripping back to SocraticBooks per-message.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0068_merge_0067_add_memory_context_data_to_message_0067_alter_artifact_artifact_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="access_code",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "Access code redeemed by the user when starting this "
                    "conversation, denormalized at create time so the billing "
                    "finalizer can resolve the matching AccessCodeGroup "
                    "(and its GroupWallet) without a callback to SocraticBooks."
                ),
                max_length=255,
                null=True,
            ),
        ),
    ]
