from django.db import migrations


def create_search_documents_tool(apps, schema_editor):
    DareTool = apps.get_model("dare_tools", "DareTool")
    DareTool._base_manager.update_or_create(
        slug="search_documents",
        defaults={
            "name": "Search Documents",
            "description": (
                "Search attached documents and shared libraries for relevant "
                "passages (agentic RAG)."
            ),
            "icon": "search",
            "category": "retrieval",
            "function_name": "search_documents",
            "is_active": True,
            "is_deleted": False,
        },
    )


def remove_search_documents_tool(apps, schema_editor):
    DareTool = apps.get_model("dare_tools", "DareTool")
    DareTool._base_manager.filter(slug="search_documents").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("dare_tools", "0002_add_create_pptx_tool"),
    ]

    operations = [
        migrations.RunPython(
            create_search_documents_tool, remove_search_documents_tool
        ),
    ]
