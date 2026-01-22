# Generated manually on 2026-01-22

from django.db import migrations


def seed_create_react_component_tool(apps, schema_editor):
    """Seed the create_react_component tool."""
    DareTool = apps.get_model('dare_tools', 'DareTool')

    # Create create_react_component tool if it doesn't exist
    # Use _default_manager since apps.get_model returns a historical model
    DareTool._default_manager.get_or_create(
        slug='create_react_component',
        defaults={
            'name': 'Create React Component',
            'description': (
                'Create interactive React components rendered in a sandboxed environment. '
                'Use for: interactive UIs, forms, dashboards, games, data visualizations, widgets. '
                'Supports React 18, Tailwind CSS, Shadcn UI components, Lucide icons, and Recharts.'
            ),
            'icon': 'code',
            'category': 'visualization',
            'function_name': 'create_react_component',
            'is_active': True,
            'is_deleted': False,
        }
    )


def reverse_seed(apps, schema_editor):
    """Remove the seeded tool."""
    DareTool = apps.get_model('dare_tools', 'DareTool')
    DareTool._default_manager.filter(slug='create_react_component').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('dare_tools', '0002_seed_update_artifact_tools'),
    ]

    operations = [
        migrations.RunPython(seed_create_react_component_tool, reverse_seed),
    ]
