from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("users", "User")
    UserWalletPreference = apps.get_model("billing", "UserWalletPreference")
    missing = User.objects.filter(wallet_preference__isnull=True).values_list(
        "pk", flat=True
    )
    UserWalletPreference.objects.bulk_create(
        [
            UserWalletPreference(user_id=pk, active_wallet_type="DARE")
            for pk in missing.iterator()
        ],
        batch_size=1000,
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0029_member_limit_alert"),
        ("users", "0039_user_background_model"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
