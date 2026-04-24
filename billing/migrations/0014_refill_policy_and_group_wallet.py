from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0013_energy_tracking"),
        ("users", "0031_accesscodegroup_group_owner"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemRefillPolicy",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "refill_amount",
                    models.DecimalField(
                        decimal_places=6,
                        default=Decimal("5.00"),
                        help_text="Platform-wide default refill amount applied to every user whose group/override does not specify one.",
                        max_digits=10,
                        verbose_name="Default Refill Amount (USD)",
                    ),
                ),
                (
                    "refill_period_days",
                    models.PositiveIntegerField(
                        default=30,
                        help_text="Platform-wide default number of days between automatic refills.",
                        verbose_name="Default Refill Period (days)",
                    ),
                ),
            ],
            options={
                "verbose_name": "System Refill Policy",
                "verbose_name_plural": "System Refill Policy",
            },
        ),
        migrations.CreateModel(
            name="GroupWallet",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "budget_balance",
                    models.DecimalField(
                        decimal_places=6,
                        default=Decimal("0.00"),
                        help_text=(
                            "Budget assigned to this group. Drained by scheduled refills and one-off "
                            "allocations to members. Refills pause when this reaches zero."
                        ),
                        max_digits=15,
                        verbose_name="Budget Balance (USD)",
                    ),
                ),
                (
                    "refill_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=6,
                        help_text="Per-member refill amount for this group. Null means inherit the system default.",
                        max_digits=10,
                        null=True,
                        verbose_name="Group Refill Amount (USD)",
                    ),
                ),
                (
                    "refill_period_days",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Days between automatic refills for members of this group. Null means inherit the system default.",
                        null=True,
                        verbose_name="Group Refill Period (days)",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="When inactive, scheduled refills are paused for all members of this group.",
                        verbose_name="Active",
                    ),
                ),
                (
                    "group",
                    models.OneToOneField(
                        help_text="The access code group this wallet configuration belongs to.",
                        on_delete=models.deletion.CASCADE,
                        related_name="group_wallet",
                        to="users.accesscodegroup",
                        verbose_name="Access Code Group",
                    ),
                ),
            ],
            options={
                "verbose_name": "Group Wallet",
                "verbose_name_plural": "Group Wallets",
            },
        ),
        migrations.CreateModel(
            name="UserRefillOverride",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "refill_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=6,
                        help_text="Custom refill amount for this user. Null means inherit from group/system.",
                        max_digits=10,
                        null=True,
                        verbose_name="Refill Amount (USD)",
                    ),
                ),
                (
                    "refill_period_days",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Custom period between refills for this user. Null means inherit from group/system.",
                        null=True,
                        verbose_name="Refill Period (days)",
                    ),
                ),
                (
                    "reason",
                    models.CharField(
                        blank=True,
                        help_text="Audit note explaining why this override exists.",
                        max_length=255,
                        verbose_name="Reason",
                    ),
                ),
                (
                    "set_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Admin or group owner who created or last updated this override.",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="refill_overrides_set",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Set By",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        help_text="The user whose refill policy is being overridden.",
                        on_delete=models.deletion.CASCADE,
                        related_name="refill_override",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User",
                    ),
                ),
            ],
            options={
                "verbose_name": "User Refill Override",
                "verbose_name_plural": "User Refill Overrides",
            },
        ),
        migrations.AddField(
            model_name="wallet",
            name="last_refill_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Timestamp of the most recent scheduled refill for this user. "
                    "Used by the scheduler to determine when the next refill is due."
                ),
                null=True,
                verbose_name="Last Refill At",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="source",
            field=models.CharField(
                choices=[
                    ("SCHEDULED_REFILL", "Scheduled refill"),
                    ("GROUP_ALLOCATION", "Group pool allocation"),
                    ("GROUP_BUDGET_TOPUP", "Group budget top-up"),
                    ("ADMIN_ADJUSTMENT", "Admin adjustment"),
                    ("REGISTRATION", "Registration credit"),
                    ("USAGE", "LLM usage debit"),
                    ("OTHER", "Other"),
                ],
                default="OTHER",
                help_text="Origin of this transaction for reporting and auditing.",
                max_length=30,
                verbose_name="Source",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="related_group",
            field=models.ForeignKey(
                blank=True,
                help_text="Access code group this transaction is associated with, if any.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="transactions",
                to="users.accesscodegroup",
                verbose_name="Related Group",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="related_transaction",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Paired transaction — for example, the informational owner row linked to a member's allocation credit."
                ),
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="related_from",
                to="billing.transaction",
                verbose_name="Related Transaction",
            ),
        ),
    ]
