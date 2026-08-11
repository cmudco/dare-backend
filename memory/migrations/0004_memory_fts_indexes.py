"""Full-text-search indexes for the memory subsystem, Postgres only.

Two expression GIN indexes:

- over ``memory_memoryrecord (key || ' ' || text)`` — the lexical branch of
  stage-one retrieval
- over ``conversations_message (message)`` — the ``search_sessions`` transcript
  tool

The query SQL in memory/services must use these expressions BYTE-FOR-BYTE
(``to_tsvector('english', key || ' ' || text)``): a COALESCE added on one side
only means Postgres silently seq-scans instead of using the index. EXPLAIN is
the check.

No ``django.contrib.postgres`` / SearchVectorField involved — raw SQL on both
the index and the query side keeps the two expressions in one pair of files
that can be diffed by eye.
"""

from django.db import connection, migrations

CREATE_INDEXES = [
    # key + text so an exact key token ("pseb", "ubl") matches even when the
    # statement paraphrases it.
    """
    CREATE INDEX IF NOT EXISTS memrec_fts_idx
    ON memory_memoryrecord
    USING GIN (to_tsvector('english', key || ' ' || text));
    """,
    """
    CREATE INDEX IF NOT EXISTS conv_message_fts_idx
    ON conversations_message
    USING GIN (to_tsvector('english', message));
    """,
]

DROP_INDEXES = [
    "DROP INDEX IF EXISTS memrec_fts_idx;",
    "DROP INDEX IF EXISTS conv_message_fts_idx;",
]


def create_fts_indexes(apps, schema_editor):
    if connection.vendor != "postgresql":
        # SQLite local dev: memory search degrades to LIKE fallbacks and the
        # read path no-ops behind USE_POSTGRES, so the indexes are not needed.
        return
    with connection.cursor() as cursor:
        for statement in CREATE_INDEXES:
            cursor.execute(statement)


def drop_fts_indexes(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for statement in DROP_INDEXES:
            cursor.execute(statement)


class Migration(migrations.Migration):

    dependencies = [
        ("memory", "0003_memory_models"),
        # conversations_message must exist; 0003 already pins the latest
        # conversations migration, carried transitively.
    ]

    operations = [
        migrations.RunPython(create_fts_indexes, drop_fts_indexes),
    ]
