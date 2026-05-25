from django.db import migrations


def create_pptx_tool(apps, schema_editor):
    DareTool = apps.get_model("dare_tools", "DareTool")
    DareTool._base_manager.update_or_create(
        slug="create_pptx",
        defaults={
            "name": "Create PPTX",
            "description": (
                "Create styled PowerPoint presentations with structured slide layouts."
            ),
            "icon": "presentation",
            "category": "visualization",
            "function_name": "create_pptx",
            "is_active": True,
            "is_deleted": False,
        },
    )


def remove_pptx_tool(apps, schema_editor):
    DareTool = apps.get_model("dare_tools", "DareTool")
    DareTool._base_manager.filter(slug="create_pptx").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("dare_tools", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_pptx_tool, remove_pptx_tool),
    ]
