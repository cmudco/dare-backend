from django.db import migrations

from core.config.vector_db import get_library_namespace

CATALOG = [
    {
        "slug": "civil-war-pensions",
        "name": "Civil War pension records",
        "description": (
            "Transcribed and chunked pension PDF pages from the U.S. Civil War "
            "- searchable as primary-source archival text."
        ),
        "curator": "CMU",
        "backend": "pinecone",
        "embedding_model": "text-embedding-3-large",
        "dims": 3072,
        "is_available": True,
    },
    {
        "slug": "case-law-corpus",
        "name": "Case law corpus",
        "description": "",
        "curator": "",
        "backend": "pinecone",
        "embedding_model": "",
        "dims": 0,
        "is_available": False,
    },
]


def seed_catalog(apps, schema_editor):
    SharedLibrary = apps.get_model("libraries", "SharedLibrary")
    for entry in CATALOG:
        SharedLibrary.objects.update_or_create(
            slug=entry["slug"],
            defaults={**entry, "namespace": get_library_namespace(entry["slug"])},
        )


def unseed_catalog(apps, schema_editor):
    SharedLibrary = apps.get_model("libraries", "SharedLibrary")
    SharedLibrary.objects.filter(slug__in=[entry["slug"] for entry in CATALOG]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("libraries", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_catalog, unseed_catalog),
    ]
