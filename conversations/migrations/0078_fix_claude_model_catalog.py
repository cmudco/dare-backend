from decimal import Decimal

from django.db import migrations

# Model IDs Anthropic has retired; requests against them return not_found_error.
RETIRED_IDENTIFIERS = [
    "claude-3-7-sonnet-20250219",  # retired 2026-02-19
    "claude-sonnet-4-20250514",  # retired 2026-06-15
]

# USD per 1M tokens (input, output) for rows seeded with 0.00 rates.
RATE_FIXES = {
    "claude-opus-4-5-20251101": (Decimal("5"), Decimal("25")),
    "claude-sonnet-4-5-20250929": (Decimal("3"), Decimal("15")),
    "claude-haiku-4-5-20251001": (Decimal("1"), Decimal("5")),
}


def fix_catalog(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    LLM.objects.filter(identifier__in=RETIRED_IDENTIFIERS).update(is_active=False)
    for identifier, (input_rate, output_rate) in RATE_FIXES.items():
        LLM.objects.filter(
            identifier=identifier,
            input_token_rate_per_million=0,
            output_token_rate_per_million=0,
        ).update(
            input_token_rate_per_million=input_rate,
            output_token_rate_per_million=output_rate,
        )


def restore_catalog(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    LLM.objects.filter(identifier__in=RETIRED_IDENTIFIERS).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0077_seed_claude_sonnet_5"),
    ]

    operations = [
        migrations.RunPython(fix_catalog, restore_catalog),
    ]
