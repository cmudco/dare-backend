from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0030_merge_20260409_1701"),
    ]

    operations = [
        migrations.AddField(
            model_name="accesscodegroup",
            name="group_owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="owned_access_code_groups",
                to=settings.AUTH_USER_MODEL,
                help_text="User who manages this group's wallet and refill policy (e.g. the professor or lab lead).",
                verbose_name="Group Owner",
            ),
        ),
    ]
