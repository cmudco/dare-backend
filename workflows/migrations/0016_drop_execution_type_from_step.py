from django.db import migrations


def drop_execution_type(apps, schema_editor):
    connection = schema_editor.connection
    vendor = connection.vendor
    with connection.cursor() as cursor:
        if vendor == 'sqlite':
            # Check if the column exists
            cursor.execute("PRAGMA table_info('workflows_step');")
            columns = [row[1] for row in cursor.fetchall()]
            if 'execution_type' not in columns:
                return
            # SQLite 3.35+ supports DROP COLUMN (without IF EXISTS)
            try:
                cursor.execute("ALTER TABLE workflows_step DROP COLUMN execution_type;")
            except Exception as exc:
                # Older SQLite versions do not support DROP COLUMN. Surface a clear error.
                raise RuntimeError(
                    'Your SQLite version does not support DROP COLUMN. '
                    'Please upgrade SQLite to >= 3.35 or recreate the local DB.'
                ) from exc
        elif vendor == 'postgresql':
            cursor.execute("ALTER TABLE workflows_step DROP COLUMN IF EXISTS execution_type;")
        else:
            # Generic attempt for other vendors
            try:
                cursor.execute("ALTER TABLE workflows_step DROP COLUMN execution_type;")
            except Exception:
                pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('workflows', '0015_merge_20250803_0348'),
    ]

    operations = [
        migrations.RunPython(drop_execution_type, noop),
    ]