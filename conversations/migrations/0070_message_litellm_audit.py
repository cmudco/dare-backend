"""
Add per-message LiteLLM audit fields to ``Message``.

When the active wallet is LITELLM, the dispatcher routes through a LiteLLM
proxy with a model name that doesn't correspond to any DB-backed
``conversations.LLM`` row (the picker emits *synthetic* entries from the
proxy's ``GET /v1/models`` probe). ``Message.llm`` therefore stays NULL for
those calls; without these audit fields we'd lose all per-message provenance
("which LiteLLM key paid for this? what model did the proxy actually run?").

The pair is populated together — either both are NULL (DARE / BYO message)
or both are set (LITELLM-routed message). Cost is always 0 for LiteLLM
messages so the existing $0-Transaction path covers billing; these fields
exist purely for the admin Usage Dashboard and audit queries.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0069_conversation_access_code"),
        ("billing", "0016_multi_wallet"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="litellm_key",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "LiteLLM key used to dispatch this message. Populated only "
                    "when wallet=LITELLM; null otherwise."
                ),
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="messages",
                to="billing.litellmkey",
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="litellm_model_name",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Model identifier sent to the LiteLLM proxy (e.g. 'gpt-4o'). "
                    "Populated only when llm is null and a LiteLLM key was used."
                ),
                max_length=255,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="message",
            name="llm",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The LLM used to generate this message (null for user "
                    "messages or LiteLLM-routed dispatches)."
                ),
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="messages",
                to="conversations.llm",
            ),
        ),
    ]
