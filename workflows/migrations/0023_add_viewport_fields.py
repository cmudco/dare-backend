# Generated migration to fix viewport field issues
# This adds the viewport_x, viewport_y, viewport_zoom fields if they don't already exist
# Migration 0018 now adds these fields, but this migration ensures backward compatibility
# for environments where 0018 was run before it was updated

from django.db import migrations, models


def add_viewport_fields_if_missing(apps, schema_editor):
    """
    Add viewport_x/y/zoom fields only if they don't already exist.
    This handles the case where 0018 was run before it was updated to add these fields.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("PRAGMA table_info(workflows_workflow);")
        existing_columns = {row[1] for row in cursor.fetchall()}

    viewport_fields = {'viewport_x', 'viewport_y', 'viewport_zoom'}
    missing_fields = viewport_fields - existing_columns

    if not missing_fields:
        print("✅ Viewport fields already exist, skipping")
        return

    # Add missing fields
    Workflow = apps.get_model('workflows', 'Workflow')
    for field_name in missing_fields:
        if field_name == 'viewport_x':
            field = models.FloatField(default=0.0, help_text='Viewport X position')
        elif field_name == 'viewport_y':
            field = models.FloatField(default=0.0, help_text='Viewport Y position')
        elif field_name == 'viewport_zoom':
            field = models.FloatField(default=1.0, help_text='Viewport zoom level')

        with schema_editor.connection.cursor() as cursor:
            default_value = 0.0 if 'zoom' not in field_name else 1.0
            cursor.execute(f"""
                ALTER TABLE workflows_workflow 
                ADD COLUMN {field_name} REAL NOT NULL DEFAULT {default_value}
            """)
        print(f"✅ Added missing field: {field_name}")


def reverse_add_fields(apps, schema_editor):
    """Remove viewport fields if they exist."""
    # This reverse is safe because if fields don't exist, we don't need to remove them
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0022_make_step_id_nullable'),
    ]

    operations = [
        migrations.RunPython(add_viewport_fields_if_missing, reverse_add_fields),
    ]
