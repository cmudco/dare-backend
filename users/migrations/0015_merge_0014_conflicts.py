from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0014_accesscodegroup_initial_wallet_credit"),
        ("users", "0014_remove_user_model_group"),
    ]

    operations = [
        # Merge migration to resolve parallel 0014 heads. No schema changes here.
    ]

