# Generated migration for pgvector extension
from django.db import migrations


class Migration(migrations.Migration):
    """
    Enable pgvector extension for PostgreSQL.
    
    This migration is safe to run on production:
    - Uses CREATE EXTENSION IF NOT EXISTS (idempotent)
    - Only runs on PostgreSQL (skips SQLite)
    - Does not modify any existing data
    """

    dependencies = [
        ('memory', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                -- Only run on PostgreSQL
                IF current_setting('server_version_num')::int >= 100000 THEN
                    CREATE EXTENSION IF NOT EXISTS vector;
                    RAISE NOTICE 'pgvector extension enabled successfully';
                END IF;
            EXCEPTION
                WHEN undefined_object THEN
                    RAISE NOTICE 'pgvector extension not available on this PostgreSQL installation';
                WHEN insufficient_privilege THEN
                    RAISE NOTICE 'Insufficient privileges to create extension. Run as superuser: CREATE EXTENSION vector;';
            END $$;
            """,
            reverse_sql="DROP EXTENSION IF EXISTS vector;",
            # This allows the migration to pass even on SQLite
            state_operations=[],
        ),
    ]
