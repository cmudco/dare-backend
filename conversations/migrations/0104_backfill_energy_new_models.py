"""
Fill energy_wh, carbon_g, water_ml on messages and transactions that were
recorded before EcoLogits could estimate their model (newer releases,
LiteLLM proxy identifiers). Only empty rows are touched.
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import Q

FIELDS = ["energy_wh", "carbon_g", "water_ml"]
BATCH_SIZE = 500


def _backfill(model, queryset, model_ref):
    from core.services.energy_service import compute_impact

    batch = []
    for row in queryset.iterator(chunk_size=BATCH_SIZE):
        provider_name, model_name = model_ref(row)
        if not model_name:
            continue
        impact = compute_impact(
            output_tokens=row.output_tokens,
            provider_name=provider_name,
            model_name=model_name,
        )
        if impact.energy_wh == 0.0:
            continue

        row.energy_wh = Decimal(str(round(impact.energy_wh, 6)))
        row.carbon_g = Decimal(str(round(impact.carbon_g, 6)))
        row.water_ml = Decimal(str(round(impact.water_ml, 6)))
        batch.append(row)

        if len(batch) >= BATCH_SIZE:
            model._default_manager.bulk_update(batch, FIELDS)
            batch = []

    if batch:
        model._default_manager.bulk_update(batch, FIELDS)


def _model_ref(row, proxy_field):
    if row.llm_id is not None:
        return row.llm.provider, row.llm.identifier
    return None, getattr(row, proxy_field)


def backfill_energy(apps, schema_editor):
    Message = apps.get_model("conversations", "Message")
    Transaction = apps.get_model("billing", "Transaction")

    messages = (
        Message._default_manager.filter(energy_wh__isnull=True, output_tokens__gt=0)
        .filter(Q(llm__isnull=False) | ~Q(litellm_model_name=""))
        .select_related("llm")
    )
    _backfill(Message, messages, lambda row: _model_ref(row, "litellm_model_name"))

    transactions = (
        Transaction._default_manager.filter(energy_wh__isnull=True, output_tokens__gt=0)
        .filter(Q(llm__isnull=False) | ~Q(llm_name=""))
        .select_related("llm")
    )
    _backfill(Transaction, transactions, lambda row: _model_ref(row, "llm_name"))


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0103_update_claude_sonnet_5_rates"),
        ("billing", "0027_unify_litellm_background_model"),
    ]

    operations = [
        migrations.RunPython(backfill_energy, migrations.RunPython.noop),
    ]
