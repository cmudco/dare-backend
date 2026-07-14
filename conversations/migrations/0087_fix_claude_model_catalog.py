from decimal import Decimal

from django.db import migrations


RETIRED_IDENTIFIERS = [
    "claude-3-7-sonnet-20250219",
    "claude-sonnet-4-20250514",
]

RATE_FIXES = {
    "claude-opus-4-5-20251101": (Decimal("5"), Decimal("25")),
    "claude-sonnet-4-5-20250929": (Decimal("3"), Decimal("15")),
    "claude-haiku-4-5-20251001": (Decimal("1"), Decimal("5")),
}


def fix_catalog(apps, schema_editor):
    llm_model = apps.get_model("conversations", "LLM")
    llm_model.objects.filter(identifier__in=RETIRED_IDENTIFIERS).update(
        is_active=False
    )
    for identifier, (input_rate, output_rate) in RATE_FIXES.items():
        llm_model.objects.filter(
            identifier=identifier,
            input_token_rate_per_million=0,
            output_token_rate_per_million=0,
        ).update(
            input_token_rate_per_million=input_rate,
            output_token_rate_per_million=output_rate,
        )


def restore_catalog(apps, schema_editor):
    llm_model = apps.get_model("conversations", "LLM")
    llm_model.objects.filter(identifier__in=RETIRED_IDENTIFIERS).update(
        is_active=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0086_seed_claude_opus_5"),
    ]

    operations = [
        migrations.RunPython(fix_catalog, restore_catalog),
    ]
