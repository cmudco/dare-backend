from decimal import Decimal

from django.db import migrations
from django.db.models import Max


OLD_TOPUP_MESSAGE = "Monthly $5 top-up"


def seed_and_backfill(apps, schema_editor):
    SystemRefillPolicy = apps.get_model("billing", "SystemRefillPolicy")
    Wallet = apps.get_model("billing", "Wallet")
    Transaction = apps.get_model("billing", "Transaction")

    # Seed singleton with current behaviour ($5 / 30 days).
    SystemRefillPolicy.objects.get_or_create(
        pk=1,
        defaults={"refill_amount": Decimal("5.00"), "refill_period_days": 30},
    )

    # Backfill Transaction.source from legacy message strings.
    Transaction.objects.filter(message=OLD_TOPUP_MESSAGE).update(source="SCHEDULED_REFILL")
    Transaction.objects.filter(message__istartswith="Initial").update(source="REGISTRATION")
    Transaction.objects.filter(source="OTHER", type=1).update(source="USAGE")  # DEBIT

    # Backfill Wallet.last_refill_at from the latest legacy top-up per user.
    latest = (
        Transaction.objects
        .filter(message=OLD_TOPUP_MESSAGE)
        .values("user_id")
        .annotate(latest_at=Max("created_at"))
    )
    for row in latest:
        Wallet.objects.filter(user_id=row["user_id"]).update(last_refill_at=row["latest_at"])


def reverse_noop(apps, schema_editor):
    # Data backfill — no reverse operation needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0014_refill_policy_and_group_wallet"),
    ]

    operations = [
        migrations.RunPython(seed_and_backfill, reverse_noop),
    ]
