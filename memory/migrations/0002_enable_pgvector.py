# Generated migration for pgvector extension
from django.db import migrations, connection


def enable_pgvector(apps, schema_editor):
    """Enable pgvector extension on PostgreSQL only."""
    if connection.vendor != 'postgresql':
        # Skip on SQLite or other databases
        return

    with connection.cursor() as cursor:
        try:
            cursor.execute('CREATE EXTENSION IF NOT EXISTS vector;')
        except Exception as e:
            # Log but don't fail - extension might need superuser
            # Use management command: python manage.py enable_pgvector
            print(f'Note: Could not create pgvector extension: {e}')
            print('Run manually: python manage.py enable_pgvector')


def disable_pgvector(apps, schema_editor):
    """Drop pgvector extension (reverse migration)."""
    if connection.vendor != 'postgresql':
        return

    with connection.cursor() as cursor:
        try:
            cursor.execute('DROP EXTENSION IF EXISTS vector;')
        except Exception:
            pass


class Migration(migrations.Migration):
    """
    Enable pgvector extension for PostgreSQL.

    This migration is safe to run on production:
    - Uses CREATE EXTENSION IF NOT EXISTS (idempotent)
    - Only runs on PostgreSQL (skips SQLite)
    - Does not modify any existing data

    If this fails due to permissions, run manually:
        python manage.py enable_pgvector
    """

    dependencies = [
        ('memory', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(enable_pgvector, disable_pgvector),
    ]
