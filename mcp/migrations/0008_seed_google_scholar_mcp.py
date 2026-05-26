import os

from django.db import migrations


def seed_google_scholar_server(apps, schema_editor):
    MCPServer = apps.get_model("mcp", "MCPServer")
    remote_url = os.getenv("GOOGLE_SCHOLAR_MCP_URL", "http://localhost:3015/mcp")

    MCPServer._default_manager.update_or_create(
        slug="google-scholar",
        defaults={
            "name": "Google Scholar",
            "description": (
                "Experimental low-volume Google Scholar discovery via a self-hosted "
                "Streamable HTTP MCP wrapper. Returns Scholar snippets, publication "
                "URLs, years, and citation counts when available."
            ),
            "icon": "google-scholar",
            "transport": "streamable_http",
            "auth_type": "none",
            "docker_image": "",
            "command": "true",
            "args": [],
            "remote_url": remote_url,
            "remote_headers": {},
            "oauth_authorize_url": "",
            "oauth_token_url": "",
            "oauth_registration_url": "",
            "oauth_scope": "",
            "oauth_client_id": "",
            "required_credentials": [],
            "credentials_help_url": "https://scholar.google.com/",
            "extra_env_vars": {},
            "setup_guide": (
                "## Google Scholar MCP\n\n"
                "This is a DARE-hosted experimental Streamable HTTP MCP wrapper for "
                "Google Scholar discovery. It uses low-volume Scholar HTML search and "
                "does not require user credentials.\n\n"
                "**Important:** Google Scholar may block, rate-limit, or CAPTCHA automated "
                "requests. Treat results as best-effort discovery, not a guaranteed "
                "production-grade scholarly index.\n\n"
                "For local development, run only the MCP sidecar and connect DARE to "
                "`http://localhost:3015/mcp`. Dockerized deployments can set "
                "`GOOGLE_SCHOLAR_MCP_URL=http://google-scholar-mcp:3015/mcp`."
            ),
            "is_active": True,
            "is_deleted": False,
        },
    )


def unseed_google_scholar_server(apps, schema_editor):
    MCPServer = apps.get_model("mcp", "MCPServer")
    MCPServer._default_manager.filter(slug="google-scholar").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("mcp", "0007_seed_remote_research_mcp_servers"),
    ]

    operations = [
        migrations.RunPython(seed_google_scholar_server, unseed_google_scholar_server),
    ]
