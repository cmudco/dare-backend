from django.db import migrations

# OpenAI lists GPT-5.6 Sol at $4.00 input / $20.00 output per 1M tokens
# (https://developers.openai.com/api/docs/pricing, checked 2026-09-02);
# the row was seeded at $5.00 / $30.00. Terra and Luna already match.
# The cached-input rate follows the family's 10% cache discount.
CURRENT_RATES = {"gpt-5.6-sol": ("4.00", "20.00", "0.4000")}
PREVIOUS_RATES = {"gpt-5.6-sol": ("5.00", "30.00", "0.5000")}


def set_rates(apps, rates):
    llm_model = apps.get_model("conversations", "LLM")
    for identifier, (input_rate, output_rate, cached_rate) in rates.items():
        llm_model.objects.filter(identifier=identifier).update(
            input_token_rate_per_million=input_rate,
            output_token_rate_per_million=output_rate,
            cached_input_token_rate_per_million=cached_rate,
        )


def update_rates(apps, schema_editor):
    set_rates(apps, CURRENT_RATES)


def restore_rates(apps, schema_editor):
    set_rates(apps, PREVIOUS_RATES)


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0097_llm_cached_input_token_rate"),
    ]

    operations = [migrations.RunPython(update_rates, restore_rates)]
