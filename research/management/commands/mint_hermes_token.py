"""
Write a fresh MCP_DARE_API_KEY into Hermes's shared ``.env``.

Hermes opens one MCP connection to DARE for the whole gateway, authenticated
with the token in ``~/.hermes/.env``. It has to be re-issued whenever DARE's JWT
scheme changes (signing key, identity claim) or the token expires:

    python manage.py mint_hermes_token --email researcher@example.com
    hermes gateway restart

The token is written straight to the file and never printed.
"""

from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from research.services.profile_service import (
    mint_service_token,
    read_env_token,
    token_names_user,
    write_env_token,
)


class Command(BaseCommand):
    help = "Write a fresh DARE MCP token into Hermes's shared .env (never printed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email", required=True, help="The DARE user Hermes acts as."
        )
        parser.add_argument(
            "--env-file",
            default=str(Path(settings.HERMES_SOUL_PATH).parent / ".env"),
            help="Hermes's shared env file (default: next to HERMES_SOUL_PATH).",
        )
        parser.add_argument(
            "--if-stale",
            action="store_true",
            help="Leave the file alone when its token still authenticates.",
        )

    def handle(self, *args, email, env_file, if_stale, **options):
        user = get_user_model().objects.filter(email=email, is_active=True).first()
        if user is None:
            raise CommandError(f"No active user with email {email}.")

        env_path = Path(env_file).expanduser()
        if if_stale and token_names_user(read_env_token(env_path), user):
            self.stdout.write(f"The token in {env_path} is still valid; unchanged.")
            return

        token = mint_service_token(user)
        write_env_token(env_path, str(token))
        expires = datetime.fromtimestamp(token["exp"], tz=timezone.utc)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote a new MCP_DARE_API_KEY for {email} to {env_path} "
                f"(valid until {expires:%Y-%m-%d}). "
                "Run `hermes gateway restart` so Hermes reconnects."
            )
        )
