import re

from django.db import migrations

# Input limits from the providers' model pages, 2026-11-26, for models 0117 left
# at the default. OpenAI's window includes the reply, so gpt-5.2 gets its input
# limit like the rest of the GPT-5 family in 0117.
INPUT_LIMITS = {
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "gemini-2.5-pro": 1_048_576,
    "gemini-3.1-flash-lite-preview": 1_048_576,
    "gpt-5.2": 272_000,
    "gpt-5.4": 922_000,
    "gpt-5.5": 922_000,
}

# Deployments pin dated snapshots (gpt-5.5-2026-04-23, claude-...-20251001);
# a snapshot has its base model's window.
SNAPSHOT_SUFFIX = r"(-\d{4}-\d{2}-\d{2}|-\d{8})?"


def seed(apps, schema_editor):
    LLM = apps.get_model("conversations", "LLM")
    for base, tokens in INPUT_LIMITS.items():
        LLM.objects.filter(
            identifier__regex=rf"^{re.escape(base)}{SNAPSHOT_SUFFIX}$"
        ).update(max_input_tokens=tokens)


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0117_llm_max_input_tokens"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
