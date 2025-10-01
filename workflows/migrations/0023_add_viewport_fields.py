# Generated migration to fix viewport field issues
# This adds the viewport_x, viewport_y, viewport_zoom fields if they don't already exist
# Migration 0018 now adds these fields, but this migration ensures backward compatibility
# for environments where 0018 was run before it was updated

from django.db import migrations, models


def get_table_columns(cursor, db_vendor, table_name):
    """Helper function to get table columns for any database"""
    if db_vendor == 'sqlite':
        cursor.execute(f"PRAGMA table_info({table_name});")
        return {row[1] for row in cursor.fetchall()}
    else:
        # PostgreSQL, MySQL, etc.
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
        """, [table_name])
        return {row[0] for row in cursor.fetchall()}


def add_viewport_fields_if_missing(apps, schema_editor):
    """
    Add viewport_x/y/zoom fields only if they don't already exist.
    This handles the case where 0018 was run before it was updated to add these fields.
    """
    db_vendor = schema_editor.connection.vendor

    with schema_editor.connection.cursor() as cursor:
        existing_columns = get_table_columns(cursor, db_vendor, 'workflows_workflow')

    viewport_fields = {'viewport_x', 'viewport_y', 'viewport_zoom'}
    missing_fields = viewport_fields - existing_columns

    if not missing_fields:
        print("✅ Viewport fields already exist, skipping")
        return

    # Add missing fields
    for field_name in missing_fields:
        default_value = 0.0 if 'zoom' not in field_name else 1.0

        with schema_editor.connection.cursor() as cursor:
            if db_vendor == 'sqlite':
                cursor.execute(f"""
                    ALTER TABLE workflows_workflow
                    ADD COLUMN {field_name} REAL NOT NULL DEFAULT {default_value}
                """)
            else:
                # PostgreSQL uses DOUBLE PRECISION instead of REAL
                cursor.execute(f"""
                    ALTER TABLE workflows_workflow
                    ADD COLUMN {field_name} DOUBLE PRECISION NOT NULL DEFAULT {default_value}
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
