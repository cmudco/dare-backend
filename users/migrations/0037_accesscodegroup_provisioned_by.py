from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0036_user_vision_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="accesscodegroup",
            name="provisioned_by",
            field=models.CharField(
                choices=[
                    ("ADMIN", "DARE admin"),
                    ("SOCRATIC_VOICE", "SocraticBooks voice assignment"),
                ],
                default="ADMIN",
                help_text="Who created this group. Service-provisioned groups (e.g. SocraticBooks voice assignment codes) are managed by that service and are refused to other callers.",
                max_length=30,
                verbose_name="Provisioned By",
            ),
        ),
    ]
