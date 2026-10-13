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

import hashlib
import hmac
import logging
from datetime import timedelta
from pathlib import Path

import django_rq
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import AccessToken

logger = logging.getLogger(__name__)

# Toolsets the api_server platform exposes inside a project profile. Deliberately
# narrow: the research agent gets memory + skills + todo + vision, and reaches
# the web only through DARE's audited MCP tools. `terminal`, `file` and
# `code_execution` are inherited by a bare profile and are NOT wanted here.
PROJECT_TOOLSETS = ["memory", "skills", "todo", "vision"]

# How often the runtime reflects in the background: every N user turns for
# memory, every N tool iterations for skills. Memory is tight so preferences are
# caught quickly; skills are loose because a Scout makes 20+ tool calls a run,
# and a tighter interval proposed the same skill patch several times per run.
MEMORY_REFLECTION_TURNS = 2
SKILL_REFLECTION_ITERATIONS = 25

_ENV_FILENAME = ".env"
_CONFIG_FILENAME = "config.yaml"
_TOKEN_VAR = "MCP_DARE_API_KEY"
_API_KEY_VAR = "API_SERVER_KEY"
_MODEL_KEY_VAR = "DARE_MODEL_API_KEY"


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


def mint_service_token(user):
    """A long-lived access token Hermes presents to DARE's MCP gateway as ``user``."""
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=timedelta(days=settings.HERMES_PROFILE_TOKEN_DAYS))
    return token


def mint_project_token(project):
    """A long-lived access token for the project's owner.

    The profile's MCP tools authenticate to DARE with this, so `web_search` /
    `fetch_page` run under the owner's account and every captured GatewayFetch
    row is attributed to the right scholar instead of one shared identity.
    """
    return str(mint_service_token(project.user))


def token_names_user(raw_token, user):
    """Whether ``raw_token`` still authenticates as ``user`` under DARE's current JWT scheme.

    False once the signing key or identity claim changes, or the token expires, so
    a stored token is re-minted instead of silently failing every tool call.
    """
    try:
        token = AccessToken(raw_token)
    except TokenError:
        return False
    return token.get(jwt_settings.USER_ID_CLAIM) == getattr(
        user, jwt_settings.USER_ID_FIELD
    )


def read_env_value(env_path, name):
    """The value of ``name`` in an env file, or "" if absent."""
    try:
        lines = Path(env_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    prefix = f"{name}="
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix) :].strip().strip("\"'")
    return ""


def write_env_value(env_path, name, value):
    """Set ``name`` in an env file, keeping every other line as it was."""
    env_path = Path(env_path)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    prefix = f"{name}="
    entry = f"{prefix}{value}"
    if any(line.startswith(prefix) for line in lines):
        lines = [entry if line.startswith(prefix) else line for line in lines]
    else:
        lines.append(entry)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(0o600)


def profile_api_key(project):
    """The bearer key DARE uses to drive ``project``'s profile on the gateway.

    Hermes fails closed on ``/p/<profile>/`` unless that profile's own ``.env``
    holds an ``API_SERVER_KEY``; it never falls back to the listener's key.
    Derived from SECRET_KEY, so it needs no storage and a rotated secret
    re-keys every profile on its next run.
    """
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        f"hermes-profile-api:{project.id}".encode(),
        hashlib.sha256,
    )
    return digest.hexdigest()[:48]


def _render_config(project):
    """The profile's config.yaml.

    Written by hand rather than via PyYAML so the file stays readable and
    reviewable — an operator opening it should see exactly the four decisions
    this profile encodes.
    """
    model = (
        getattr(project, "hermes_model", "") or settings.HERMES_PROFILE_MODEL or ""
    ).strip()
    provider = (settings.HERMES_PROFILE_MODEL_PROVIDER or "").strip()
    if provider and not model:
        # The expensive trapdoor: with a provider set and no model, Hermes falls
        # through to entry [0] of that provider's curated list, which for
        # anthropic is the flagship (claude-fable-5). It guards metered
        # aggregators against this but not the direct providers, so a blank
        # model here would silently bill the priciest model on every run.
        # Naming no provider at all is safe — it resolves to "" and inherits.
        raise ImproperlyConfigured(
            "HERMES_PROFILE_MODEL_PROVIDER is set but HERMES_PROFILE_MODEL is "
            "empty. Set a model explicitly, or clear the provider — never ship "
            "a provider with no model."
        )

    toolsets = "\n".join(f"    - {name}" for name in PROJECT_TOOLSETS)
    base_url = (settings.HERMES_PROFILE_MODEL_BASE_URL or "").strip()
    if base_url:
        provider = "custom"
    model_block = (
        # Pinned on purpose: an unpinned profile follows whatever the runtime
        # default happens to be, so `hermes model` on the box would re-point
        # every research project at once — and could re-point it at the flagship.
        "model:\n"
        f"  default: {model}\n" + (f"  provider: {provider}\n" if provider else "")
        if model
        else ""
    )
    if model and base_url:
        model_block += (
            f"  base_url: {base_url}\n"
            f"  api_key: ${{{_MODEL_KEY_VAR}}}\n"
            "  api_mode: chat_completions\n"
        )
    fallback_block = "".join(
        f"  - provider: {provider}\n    model: {fallback}\n"
        for provider, fallback in settings.HERMES_PROFILE_FALLBACK_MODELS
    )
    if fallback_block:
        fallback_block = (
            "# Switched to, in order, when the pinned model is rate-limited or\n"
            "# down — free tiers hit upstream capacity limits often.\n"
            "fallback_model:\n" + fallback_block + "\n"
        )
    return (
        f"# Hermes profile for DARE research project {project.id}\n"
        f"# {project.title}\n"
        "#\n"
        "# Generated by DARE (research/services/profile_service.py) and rewritten\n"
        "# whenever it drifts from the template — edit DARE, not this file.\n"
        "\n"
        f"{model_block}"
        "\n"
        f"{fallback_block}"
        "# Tiered disclosure hides the MCP tools behind `tool_search` and makes\n"
        "# the agent go find them. That trade is wrong here: a project profile\n"
        "# exposes two gateway tools, so the extra hop buys nothing and costs a\n"
        "# turn per lookup — observed a run spend four failed `tool_call` hops\n"
        "# before falling back to calling mcp__dare__* directly.\n"
        "tools:\n"
        "  tool_search:\n"
        "    enabled: false\n"
        "\n"
        "# A Scout searches, reads and stages one tool call at a time; a low\n"
        "# global ceiling ends it mid-search with nothing staged.\n"
        "agent:\n"
        f"  max_turns: {settings.HERMES_PROFILE_MAX_TURNS}\n"
        "\n"
        "# The learning loop runs, but nothing it learns is committed on its own:\n"
        "# every memory and skill write — foreground or the background reflection\n"
        "# fork — is staged under pending/ for DARE to put in front of the scholar.\n"
        "# DARE renders what they approve back into memories/ and skills/dare/.\n"
        "memory:\n"
        "  memory_enabled: true\n"
        "  user_profile_enabled: true\n"
        "  write_approval: true\n"
        f"  nudge_interval: {MEMORY_REFLECTION_TURNS}\n"
        "\n"
        "skills:\n"
        "  write_approval: true\n"
        f"  creation_nudge_interval: {SKILL_REFLECTION_ITERATIONS}\n"
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


def _write_if_changed(path, content):
    """Write ``content`` only when it differs from what is on disk.

    Idempotent so provisioning can run on every request, while still letting a
    change to the template (a new pinned model, a different toolset) reach
    profiles that already exist.
    """
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    path.write_text(content, encoding="utf-8")
    return True


def _schedule_warmup(project):
    """Have a new profile connect its DARE tools before the user's first turn.

    Hermes connects a profile's MCP servers in the background on its first
    run, a few seconds after that run starts, so a brand-new project's first
    message would otherwise run without search or Scouts.
    """
    project_id = project.id

    def enqueue():
        try:
            django_rq.get_queue("default").enqueue(
                "research.tasks.warm_profile_job", project_id
            )
        except Exception:  # noqa: BLE001 - warm-up is an optimisation
            logger.warning("Could not schedule warm-up for project %s", project_id)

    transaction.on_commit(enqueue)


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
    is_new = not home.exists()
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / "memories").mkdir(exist_ok=True)

        _write_if_changed(home / _CONFIG_FILENAME, _render_config(project))

        env_path = home / _ENV_FILENAME
        if refresh_token or not token_names_user(
            read_env_value(env_path, _TOKEN_VAR), project.user
        ):
            write_env_value(env_path, _TOKEN_VAR, mint_project_token(project))
            logger.info("Minted a fresh MCP token for Hermes profile %s", name)
        api_key = profile_api_key(project)
        if read_env_value(env_path, _API_KEY_VAR) != api_key:
            write_env_value(env_path, _API_KEY_VAR, api_key)
        model_key = settings.HERMES_PROFILE_MODEL_API_KEY
        if model_key and read_env_value(env_path, _MODEL_KEY_VAR) != model_key:
            write_env_value(env_path, _MODEL_KEY_VAR, model_key)
    except OSError as exc:
        # Provisioning is best-effort: a project that cannot get its own profile
        # must still work, so fall back to the shared default rather than 500.
        logger.warning(
            "Could not provision Hermes profile %s at %s: %s", name, home, exc
        )
        return ""

    if is_new:
        _schedule_warmup(project)

    base_url = profile_base_url_for(project)
    if project.hermes_base_url != base_url:
        project.hermes_base_url = base_url
        project.save(update_fields=["hermes_base_url", "updated_at"])
        logger.info("Project %s bound to Hermes profile %s", project.id, name)

    return name
