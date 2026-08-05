from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import URLValidator

from mcp.models import MCPServer


class Command(BaseCommand):
    help = "Configure the Quillmark MCP URL for this deployment"

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default="http://127.0.0.1:8090/mcp",
            help="Backend-reachable Quillmark streamable HTTP endpoint",
        )
        parser.add_argument(
            "--disable",
            action="store_true",
            help="Disable Quillmark instead of activating it",
        )

    def handle(self, *args, **options):
        remote_url = options["url"]
        try:
            URLValidator(schemes=["http", "https"])(remote_url)
        except ValidationError as exc:
            raise CommandError(f"Invalid Quillmark URL: {remote_url}") from exc

        server = MCPServer.all_objects.filter(slug="quillmark").first()
        if not server:
            raise CommandError(
                "Quillmark MCP server is not seeded; run migrations first"
            )

        server.remote_url = remote_url
        server.is_active = not options["disable"]
        server.is_deleted = False
        server.save(
            update_fields=["remote_url", "is_active", "is_deleted", "updated_at"]
        )
        state = "disabled" if options["disable"] else "active"
        self.stdout.write(self.style.SUCCESS(f"Quillmark is {state} at {remote_url}"))
