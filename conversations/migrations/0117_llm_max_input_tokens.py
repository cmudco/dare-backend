from django.db import migrations, models

# Input limits from the providers' model pages, 2026-11-24. OpenAI's window
# includes the reply, so its stated input limit is used.
MAX_INPUT_TOKENS = {
    "claude-fable-5": 1_000_000,
    "claude-fable-5-1": 1_000_000,
    "claude-fable-5-2": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-opus-5-5": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-5-5": 1_000_000,
    "claude-haiku-5-5": 1_000_000,
    "claude-opus-4-5-20251101": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-flash-lite": 1_048_576,
    "gemini-3.1-flash-lite": 1_048_576,
    "gemini-3.1-pro-preview": 1_048_576,
    "gemini-3.5-flash": 1_048_576,
    "gemini-3.5-flash-lite": 1_048_576,
    "gemini-3.7-flash": 1_048_576,
    "gemini-3.8-flash": 1_048_576,
    "gemini-4-argon": 1_048_576,
    "gemini-4-carbon": 1_048_576,
    "gemini-4-hydrogen": 1_048_576,
    "gemini-4-neon": 1_048_576,
    "gpt-6-astra": 922_000,
    "gpt-6-sol": 922_000,
    "gpt-6-luna": 922_000,
    "gpt-6.1-sol": 922_000,
    "gpt-6.1-terra": 922_000,
    "gpt-5.6-sol": 922_000,
    "gpt-5.6-terra": 922_000,
    "gpt-5.6-luna": 922_000,
    "gpt-5.5": 922_000,
    "gpt-5.4": 922_000,
    "gpt-5.4-mini": 272_000,
    "gpt-5.1-2025-11-13": 272_000,
    "gpt-5": 272_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-3.5-turbo": 16_385,
}


def seed(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    for identifier, tokens in MAX_INPUT_TOKENS.items():
        LLM.objects.filter(identifier=identifier).update(max_input_tokens=tokens)


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0116_seed_gemini_4_neon_effort_ceilings"),
    ]

    operations = [
        migrations.AddField(
            model_name="llm",
            name="max_input_tokens",
            field=models.PositiveIntegerField(
                default=200000,
                help_text=(
                    "Largest prompt the model accepts, in tokens. Picked files fit "
                    "for reading in full while they use at most half of it."
                ),
            ),
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
