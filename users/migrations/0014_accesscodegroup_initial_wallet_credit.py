from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0013_accesscodegroup_model_group"),
    ]

    operations = [
        migrations.AddField(
            model_name="accesscodegroup",
            name="initial_wallet_credit",
            field=models.DecimalField(
                max_digits=10,
                decimal_places=6,
                null=True,
                blank=True,
                help_text=(
                    "Optional initial wallet credit (USD) to grant new users who register with this access code. "
                    "If left blank, normal defaults apply."
                ),
                verbose_name=("Initial Wallet Credit (USD)"),
            ),
        ),
    ]

