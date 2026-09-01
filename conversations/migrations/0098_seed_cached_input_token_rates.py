from decimal import Decimal

from django.db import migrations

# Cached-input price as a fraction of the model's own input rate, from the
# providers' published cache pricing. Deriving from the row's input rate
# keeps the value correct in every environment regardless of the absolute
# prices configured there.
#
#   OpenAI:    gpt-4o family 50%, gpt-4.1 family 25%, gpt-5 / o-series 10%
#   Anthropic: cache reads are 10% of input on every model
#   Google:    Gemini 2.5 Pro 25%, 2.0 family 25%, 2.5 Flash / Flash-Lite and
#              Gemini 3.x 10%
#
# Longest prefix wins. Models with no entry (image, audio, gpt-3.5, unknown
# custom rows) are left untouched and bill cached tokens at the input rate.
CACHE_DISCOUNT_BY_PREFIX = {
    "gpt-4o": Decimal("0.50"),
    "gpt-4.1": Decimal("0.25"),
    "gpt-5": Decimal("0.10"),
    "o1": Decimal("0.50"),
    "o3": Decimal("0.25"),
    "o4": Decimal("0.25"),
    "claude": Decimal("0.10"),
    "gemini-2.0": Decimal("0.25"),
    "gemini-2.5-pro": Decimal("0.25"),
    "gemini-2.5": Decimal("0.10"),
    "gemini-3": Decimal("0.10"),
}

EXCLUDED_PREFIXES = ("dall-e", "whisper", "gpt-4o-transcribe", "gpt-image")


def _discount_for(identifier: str):
    ident = identifier.lower()
    if ident.startswith(EXCLUDED_PREFIXES):
        return None
    match = max(
        (prefix for prefix in CACHE_DISCOUNT_BY_PREFIX if ident.startswith(prefix)),
        key=len,
        default=None,
    )
    return CACHE_DISCOUNT_BY_PREFIX[match] if match else None


def seed_cached_input_rates(apps, schema_editor):
    llm_model = apps.get_model("conversations", "LLM")
    rows = llm_model.objects.filter(
        cached_input_token_rate_per_million__isnull=True,
        input_token_rate_per_million__gt=0,
    )
    for llm in rows:
        discount = _discount_for(llm.identifier)
        if discount is None:
            continue
        llm.cached_input_token_rate_per_million = (
            llm.input_token_rate_per_million * discount
        ).quantize(Decimal("0.0001"))
        llm.save(update_fields=["cached_input_token_rate_per_million"])


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0097_llm_cached_input_token_rate"),
    ]

    operations = [
        migrations.RunPython(seed_cached_input_rates, migrations.RunPython.noop)
    ]
