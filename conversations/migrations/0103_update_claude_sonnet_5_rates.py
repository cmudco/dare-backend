from django.db import migrations

# https://platform.claude.com/docs/en/about-claude/pricing (Model pricing,
# footnote 3): "The $2/$10 per million input/output token pricing for Claude
# Sonnet 5, announced at launch as introductory pricing through August 31,
# 2026, is now the standard price. The previously scheduled increase to $3/$15
# per million input/output tokens on September 1, 2026 will not occur."
# Cache hits use the standard 0.1x multiplier: $0.20 / MTok.
# 0077 seeded the post-promo $3/$15, so every environment has been over-billing.
SONNET_5_IDENTIFIER = "claude-sonnet-5"
SONNET_5_RATES = {
    "input_token_rate_per_million": "2.00",
    "output_token_rate_per_million": "10.00",
    "cached_input_token_rate_per_million": "0.2000",
}


def update_sonnet_5_rates(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    updated = LLM.objects.filter(identifier=SONNET_5_IDENTIFIER).update(
        **SONNET_5_RATES
    )
    print(f"\nClaude Sonnet 5 rate update: {updated} row(s) updated\n")


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0102_seed_gpt_6_opus_5_5_fable_5_1"),
    ]

    operations = [
        # Reverse is a no-op: rolling back must not restore a wrong price.
        migrations.RunPython(update_sonnet_5_rates, migrations.RunPython.noop),
    ]
