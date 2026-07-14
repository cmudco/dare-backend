from django.db import migrations


def enable_artifacts(apps, schema_editor):
    """Artifacts power the CMU document-generation experience (inline PDF
    previews in the artifact panel) — dark by default made rendered documents
    invisible even though generation succeeded."""
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.update_or_create(
        key="enable_artifacts",
        defaults={
            "description": (
                "Artifact generation and the artifact side panel (charts, "
                "documents, presentations, rendered PDFs)."
            ),
            "default_enabled": True,
        },
    )


def disable_artifacts(apps, schema_editor):
    FeatureFlag = apps.get_model("feature_flags", "FeatureFlag")
    FeatureFlag.objects.filter(key="enable_artifacts").update(default_enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ("feature_flags", "0005_seed_research_flag"),
    ]

    operations = [
        migrations.RunPython(enable_artifacts, disable_artifacts),
    ]
