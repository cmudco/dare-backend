from django.db import migrations

SLUGS = ["civil-war-pensions", "case-law-corpus"]


def to_weaviate(apps, schema_editor):
    SharedLibrary = apps.get_model("libraries", "SharedLibrary")
    SharedLibrary.objects.filter(slug__in=SLUGS).update(backend="weaviate")


def to_pinecone(apps, schema_editor):
    SharedLibrary = apps.get_model("libraries", "SharedLibrary")
    SharedLibrary.objects.filter(slug__in=SLUGS).update(backend="pinecone")


class Migration(migrations.Migration):
    dependencies = [
        ("libraries", "0002_seed_catalog"),
    ]

    operations = [
        migrations.RunPython(to_weaviate, to_pinecone),
    ]
