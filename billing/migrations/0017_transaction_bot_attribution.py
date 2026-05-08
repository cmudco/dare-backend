"""
Transaction: bot attribution + wallet-router fallback reason.

Adds the columns needed by the bot billing track:

- ``bot_id`` (PositiveInteger, indexed) — Socratic Bot ID. SocraticBooks owns
  the Bot table, so we keep this as a plain int (no FK across services).
- ``bot_owner`` (FK User, indexed) — DARE user who owns the bot. Lets the
  owner usage dashboard filter "transactions where I'm the bot owner"
  without joining through SocraticBooks.
- ``fallback_reason`` (short string) — populated by the wallet router when
  it cannot honor the user's preferred wallet (e.g. BYO key missing for the
  requested provider, LiteLLM key expired) and falls back to DARE. Closes
  the audit blind spot where a BYO-mode user would see DARE charges with
  no recorded explanation.

Renamed for accuracy from the original soft-delete-LiteLLMKey scope; that
piece was reverted as out-of-scope for the Tier-1 hardening pass.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0016_multi_wallet"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="bot_id",
            field=models.PositiveIntegerField(
                blank=True,
                db_index=True,
                help_text=(
                    "Socratic Bot ID (SocraticBooks-owned), set when the call "
                    "was made through a deployed bot."
                ),
                null=True,
                verbose_name="Bot ID",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="bot_owner",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "DARE user who owns the Socratic Bot this call was "
                    "attributed to. Used by the owner usage dashboard."
                ),
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="bot_owned_transactions",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Bot Owner",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="fallback_reason",
            field=models.CharField(
                blank=True,
                help_text=(
                    "When the wallet router couldn't honor the user's preferred "
                    "wallet (e.g. BYO_PROVIDER_MISSING, LITELLM_EXPIRED), this "
                    "records why DARE billed instead."
                ),
                max_length=64,
                null=True,
                verbose_name="Fallback Reason",
            ),
        ),
    ]
