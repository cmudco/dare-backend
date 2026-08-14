from django.db import migrations

CURRENT_RATES = {
    "gpt-5.6-terra": ("2.00", "12.00"),
    "gpt-5.6-luna": ("0.20", "1.20"),
}
PREVIOUS_RATES = {
    "gpt-5.6-terra": ("2.50", "15.00"),
    "gpt-5.6-luna": ("1.00", "6.00"),
}


def set_rates(apps, rates):
    llm_model = apps.get_model("conversations", "LLM")
    for identifier, (input_rate, output_rate) in rates.items():
        llm_model.objects.filter(identifier=identifier).update(
            input_token_rate_per_million=input_rate,
            output_token_rate_per_million=output_rate,
        )


def update_rates(apps, schema_editor):
    set_rates(apps, CURRENT_RATES)


def restore_rates(apps, schema_editor):
    set_rates(apps, PREVIOUS_RATES)


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0092_remove_conversation_last_memory_extracted_at")
    ]

    operations = [migrations.RunPython(update_rates, restore_rates)]
