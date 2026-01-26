# Generated manually on 2026-01-22

from django.db import migrations


def seed_update_artifact_tools(apps, schema_editor):
    """Seed the update_artifact and update_artifact_inline tools."""
    DareTool = apps.get_model('dare_tools', 'DareTool')

    # Create update_artifact tool if it doesn't exist
    DareTool.active_objects.get_or_create(
        slug='update_artifact',
        defaults={
            'name': 'Update Artifact',
            'description': (
                'Update an existing artifact (diagram, chart, etc.) by creating a new version '
                'with modified content. Use for major rewrites affecting >30% of content.'
            ),
            'icon': 'edit',
            'category': 'visualization',
            'function_name': 'update_artifact',
            'is_active': True,
            'is_deleted': False,
        }
    )

    # Create update_artifact_inline tool if it doesn't exist
    DareTool.active_objects.get_or_create(
        slug='update_artifact_inline',
        defaults={
            'name': 'Update Artifact Inline',
            'description': (
                'Make targeted string replacements in an existing artifact for small edits. '
                'Use for: fixing typos, changing colors, updating single values.'
            ),
            'icon': 'edit-inline',
            'category': 'visualization',
            'function_name': 'update_artifact_inline',
            'is_active': True,
            'is_deleted': False,
        }
    )


def reverse_seed(apps, schema_editor):
    """Remove the seeded tools."""
    DareTool = apps.get_model('dare_tools', 'DareTool')
    DareTool.active_objects.filter(
        slug__in=['update_artifact', 'update_artifact_inline']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('dare_tools', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_update_artifact_tools, reverse_seed),
    ]
