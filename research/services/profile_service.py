"""
Per-project Hermes profiles — the isolation boundary for agent memory.

A Hermes profile is simply its own ``HERMES_HOME`` directory. Everything the
runtime keeps per profile resolves from that root: ``SOUL.md``, the memory tool's
``memories/{MEMORY,USER}.md`` (``get_hermes_home() / "memories"`` in Hermes's
``tools/memory_tool.py``), sessions, and ``.env``. So giving each project its own
directory makes its agent memory isolated *by construction* — not by a naming
convention we have to keep honouring.

Two things make this cheap. The gateway's multiplex mode serves every profile
from the one listener under ``/p/<profile>/``, so N projects still means one
process and one port. And a profile needs nothing but its directory to exist —
Hermes resolves it, inherits the default config, and creates the rest on demand.
We still write a config so the toolset and MCP surface are deliberate rather
than inherited.

The profile's ``.env`` carries a DARE-minted JWT for the project's owner, so the
audited web tools run as that scholar. Without it a profile's tools resolve no
credential at all: Hermes does not fall back to the default profile's ``.env``.
"""

import logging
from datetime import timedelta
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Toolsets the api_server platform exposes inside a project profile. Deliberately
# narrow: the research agent gets memory + skills + todo + vision, and reaches
# the web only through DARE's audited MCP tools. `terminal`, `file` and
# `code_execution` are inherited by a bare profile and are NOT wanted here.
PROJECT_TOOLSETS = ["memory", "skills", "todo", "vision"]

_ENV_FILENAME = ".env"
_CONFIG_FILENAME = "config.yaml"
_TOKEN_VAR = "MCP_DARE_API_KEY"


def profile_name_for(project):
    """The Hermes profile name for ``project`` (matches [a-z0-9][a-z0-9_-]{0,63})."""
    return f"{settings.HERMES_PROFILE_PREFIX}{project.id}"


def profile_home_for(project):
    """The profile's HERMES_HOME directory — where its soul and memory live."""
    return Path(settings.HERMES_PROFILES_ROOT) / profile_name_for(project)


def profile_base_url_for(project):
    """The project's gateway URL — the shared listener, its own profile prefix."""
    gateway = settings.HERMES_GATEWAY_URL.rstrip("/")
    return f"{gateway}/p/{profile_name_for(project)}/"


def mint_project_token(project):
    """A long-lived access token for the project's owner.

    The profile's MCP tools authenticate to DARE with this, so `web_search` /
    `fetch_page` run under the owner's account and every captured GatewayFetch
    row is attributed to the right scholar instead of one shared identity.
    """
    from rest_framework_simplejwt.tokens import AccessToken

    token = AccessToken.for_user(project.user)
    token.set_exp(lifetime=timedelta(days=settings.HERMES_PROFILE_TOKEN_DAYS))
    return str(token)


def _render_config(project):
    """The profile's config.yaml.

    Written by hand rather than via PyYAML so the file stays readable and
    reviewable — an operator opening it should see exactly the four decisions
    this profile encodes.
    """
    toolsets = "\n".join(f"    - {name}" for name in PROJECT_TOOLSETS)
    return (
        f"# Hermes profile for DARE research project {project.id}\n"
        f"# {project.title}\n"
        "#\n"
        "# Managed by DARE (research/services/profile_service.py). Edits here are\n"
        "# preserved on re-provision except for the blocks DARE owns below.\n"
        "\n"
        "platform_toolsets:\n"
        "  api_server:\n"
        f"{toolsets}\n"
        "\n"
        "mcp_servers:\n"
        "  dare:\n"
        f"    url: {settings.DARE_MCP_GATEWAY_URL}\n"
        "    transport: http\n"
        "    enabled: true\n"
        "    headers:\n"
        f"      Authorization: Bearer ${{{_TOKEN_VAR}}}\n"
    )


def _write_if_absent(path, content, *, mode=None):
    """Write ``content`` to ``path`` unless it already exists. Returns True if written."""
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return True


def ensure_project_profile(project, *, refresh_token=False):
    """Make sure ``project`` has a Hermes profile on disk, and return its name.

    Idempotent: safe to call on every run. Creates the directory, writes the
    config and the owner-scoped token on first use, and records the gateway URL
    on the project so ``get_hermes_service(project)`` routes there from then on.

    Returns "" when per-project profiles are disabled, which leaves every caller
    on the shared default profile — the pre-multiplex behaviour.
    """
    if not settings.HERMES_PROFILE_PER_PROJECT:
        return ""

    name = profile_name_for(project)
    home = profile_home_for(project)
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / "memories").mkdir(exist_ok=True)

        _write_if_absent(home / _CONFIG_FILENAME, _render_config(project))

        env_path = home / _ENV_FILENAME
        if refresh_token or not env_path.exists():
            # 0600: this file holds a year-long token for the project's owner.
            env_path.write_text(
                f"{_TOKEN_VAR}={mint_project_token(project)}\n", encoding="utf-8"
            )
            env_path.chmod(0o600)
    except OSError as exc:
        # Provisioning is best-effort: a project that cannot get its own profile
        # must still work, so fall back to the shared default rather than 500.
        logger.warning(
            "Could not provision Hermes profile %s at %s: %s", name, home, exc
        )
        return ""

    base_url = profile_base_url_for(project)
    if project.hermes_base_url != base_url:
        project.hermes_base_url = base_url
        project.save(update_fields=["hermes_base_url", "updated_at"])
        logger.info("Project %s bound to Hermes profile %s", project.id, name)

    return name
