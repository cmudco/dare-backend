from django.db import migrations


def seed_quillmark_server(apps, schema_editor):
    MCPServer = apps.get_model("mcp", "MCPServer")

    MCPServer._default_manager.update_or_create(
        slug="quillmark",
        defaults={
            "name": "CMU Documents (Quillmark)",
            "description": (
                "Generate polished, CMU-brand-compliant PDF documents from "
                "conversation content: official letterhead memos, reports with "
                "cover pages, and one-page briefs. Rendered with Typst via the "
                "Quillmark engine."
            ),
            "icon": "quillmark",
            "transport": "streamable_http",
            "auth_type": "none",
            "docker_image": "",
            "command": "true",
            "args": [],
            "remote_url": "http://quillmark-mcp:8080/mcp",
            "remote_headers": {},
            "oauth_authorize_url": "",
            "oauth_token_url": "",
            "oauth_registration_url": "",
            "oauth_scope": "",
            "oauth_client_id": "",
            "required_credentials": [],
            "credentials_help_url": "",
            "extra_env_vars": {},
            "setup_guide": (
                "## CMU Documents (Quillmark)\n\n"
                "Runs as the `quillmark-mcp` service in this deployment's Docker "
                "Compose stack — no credentials required. Click **Connect** and "
                "select the server in the chat composer.\n\n"
                'Ask for a document in plain language ("turn this into a CMU memo '
                'to the dean") or pick a template from the Documents menu. '
                "Available templates live in the mounted CMU quiver; drop a new "
                "quill directory there and restart the service to add formats."
            ),
            "is_active": True,
            "is_deleted": False,
        },
    )


def remove_quillmark_server(apps, schema_editor):
    MCPServer = apps.get_model("mcp", "MCPServer")
    MCPServer._default_manager.filter(slug="quillmark").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("mcp", "0011_gatewayfetch_call_id"),
    ]

    operations = [
        migrations.RunPython(seed_quillmark_server, remove_quillmark_server),
    ]
