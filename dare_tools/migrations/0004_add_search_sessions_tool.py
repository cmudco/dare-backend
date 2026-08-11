from django.db import migrations


def create_search_sessions_tool(apps, schema_editor):
    DareTool = apps.get_model("dare_tools", "DareTool")
    DareTool._base_manager.update_or_create(
        slug="search_sessions",
        defaults={
            "name": "Search Past Conversations",
            "description": (
                "Search the verbatim transcript of the user's past "
                "conversations (episodic memory)."
            ),
            "icon": "history",
            "category": "retrieval",
            "function_name": "search_sessions",
            "is_active": True,
            "is_deleted": False,
        },
    )


def remove_search_sessions_tool(apps, schema_editor):
    DareTool = apps.get_model("dare_tools", "DareTool")
    DareTool._base_manager.filter(slug="search_sessions").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("dare_tools", "0003_add_search_documents_tool"),
    ]

    operations = [
        migrations.RunPython(create_search_sessions_tool, remove_search_sessions_tool),
    ]
