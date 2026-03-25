from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("prompts", "0005_change_prompt_on_delete_to_set_null"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="prompt",
            name="forked_from_user",
            field=models.ForeignKey(
                blank=True,
                help_text="Original owner when this prompt was cloned from another user.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="shared_prompt_copies",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
