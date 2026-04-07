import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("sharing", "0002_alter_shareditem_object_id"),
        ("users", "0006_accesscodegroup"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Drop the old unique_together constraint
        migrations.AlterUniqueTogether(
            name="shareditem",
            unique_together=set(),
        ),
        # Make shared_with nullable
        migrations.AlterField(
            model_name="shareditem",
            name="shared_with",
            field=models.ForeignKey(
                blank=True,
                help_text="Specific user who received the share (null for group shares)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="items_shared_with_me",
                to=settings.AUTH_USER_MODEL,
                verbose_name="shared with",
            ),
        ),
        # Add shared_with_group FK
        migrations.AddField(
            model_name="shareditem",
            name="shared_with_group",
            field=models.ForeignKey(
                blank=True,
                help_text="Access code group this item is shared with (null for individual shares)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shared_items",
                to="users.accesscodegroup",
            ),
        ),
        # Add index for shared_with_group
        migrations.AddIndex(
            model_name="shareditem",
            index=models.Index(
                fields=["shared_with_group", "content_type"],
                name="sharing_sha_shared__group_ct_idx",
            ),
        ),
        # Add partial unique constraints replacing unique_together
        migrations.AddConstraint(
            model_name="shareditem",
            constraint=models.UniqueConstraint(
                condition=Q(shared_with__isnull=False),
                fields=["content_type", "object_id", "shared_with"],
                name="unique_individual_share",
            ),
        ),
        migrations.AddConstraint(
            model_name="shareditem",
            constraint=models.UniqueConstraint(
                condition=Q(shared_with_group__isnull=False),
                fields=["content_type", "object_id", "shared_with_group"],
                name="unique_group_share",
            ),
        ),
    ]
