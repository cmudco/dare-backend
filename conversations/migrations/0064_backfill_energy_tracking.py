"""
Data migration to backfill energy_wh, carbon_g, water_ml on existing messages.

Iterates over AI messages that have output_tokens > 0 and energy_wh IS NULL,
computes environmental impact using EcoLogits, and stores the results.
"""

from django.db import migrations


def backfill_energy(apps, schema_editor):
    from core.services.energy_service import compute_impact
    from decimal import Decimal

    Message = apps.get_model("conversations", "Message")

    messages = (
        Message._default_manager
        .filter(energy_wh__isnull=True, output_tokens__gt=0, llm__isnull=False)
        .select_related("llm")
        .only("id", "output_tokens", "llm__provider", "llm__identifier", "energy_wh", "carbon_g", "water_ml")
    )

    batch = []
    for msg in messages.iterator(chunk_size=500):
        impact = compute_impact(
            output_tokens=msg.output_tokens,
            provider_name=msg.llm.provider,
            model_name=msg.llm.identifier,
        )
        if impact.energy_wh == 0.0:
            continue

        msg.energy_wh = Decimal(str(round(impact.energy_wh, 6)))
        msg.carbon_g = Decimal(str(round(impact.carbon_g, 6)))
        msg.water_ml = Decimal(str(round(impact.water_ml, 6)))
        batch.append(msg)

        if len(batch) >= 500:
            Message._default_manager.bulk_update(batch, ["energy_wh", "carbon_g", "water_ml"])
            batch = []

    if batch:
        Message._default_manager.bulk_update(batch, ["energy_wh", "carbon_g", "water_ml"])


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0063_energy_tracking"),
    ]

    operations = [
        migrations.RunPython(backfill_energy, migrations.RunPython.noop),
    ]
