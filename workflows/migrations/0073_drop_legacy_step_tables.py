from django.db import migrations

LEGACY_STEP_TABLES = (
    "workflows_workflow_steps",
    "workflows_step_embeddings",
    "workflows_step_files",
    "workflows_step",
)


def drop_legacy_step_schema(apps, schema_editor):
    """Remove the pre-node workflow schema left behind by migration 0027."""
    connection = schema_editor.connection
    quote = schema_editor.quote_name

    with connection.cursor() as cursor:
        table_names = set(connection.introspection.table_names(cursor))
        run_step_table = "workflows_workflowrunstep"
        if run_step_table in table_names:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, run_step_table
                )
            }
            for column_name in ("step_id", "step_id_id"):
                if column_name in columns:
                    schema_editor.execute(
                        f"ALTER TABLE {quote(run_step_table)} "
                        f"DROP COLUMN {quote(column_name)}"
                    )

        cascade = " CASCADE" if connection.vendor == "postgresql" else ""
        for table_name in LEGACY_STEP_TABLES:
            schema_editor.execute(f"DROP TABLE IF EXISTS {quote(table_name)}{cascade}")


class Migration(migrations.Migration):
    dependencies = [
        ("workflows", "0072_stepnodedata_enable_artifacts"),
    ]

    operations = [
        migrations.RunPython(drop_legacy_step_schema, migrations.RunPython.noop),
    ]
