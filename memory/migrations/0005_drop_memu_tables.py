"""Drop the tables MemU created for itself.

memu-py ran with ``ddl_mode: create`` and made its own tables in the DARE
database, outside Django migrations — so no Django state knows about them and
removing the package leaves them orphaned. The names are fixed by
memu.database.postgres.models: memory_items, memory_categories, category_items
(the M2M), and resources.

Forward-only: the data was MemU-extracted summaries the greenfield system
replaces wholesale, and the package that could read them is gone.
"""

from django.db import connection, migrations

# category_items first — it holds FKs into the other two.
DROP_TABLES = """
DROP TABLE IF EXISTS category_items CASCADE;
DROP TABLE IF EXISTS memory_items CASCADE;
DROP TABLE IF EXISTS memory_categories CASCADE;
DROP TABLE IF EXISTS resources CASCADE;
"""


def drop_memu_tables(apps, schema_editor):
    if connection.vendor != "postgresql":
        # MemU only ever wrote to Postgres (its service was USE_POSTGRES-gated).
        return
    with connection.cursor() as cursor:
        cursor.execute(DROP_TABLES)


class Migration(migrations.Migration):

    dependencies = [
        ("memory", "0004_memory_fts_indexes"),
    ]

    operations = [
        migrations.RunPython(drop_memu_tables, migrations.RunPython.noop),
    ]
