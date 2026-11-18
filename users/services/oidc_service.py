"""Single sign-on through the deployment's OpenID Connect identity provider."""

import secrets

import jwt
from django.contrib.auth import get_user_model
from django.core.cache import cache

from config import env
from users.models import AccessCodeGroup
from users.role_service import role_for_groups
from users.services.onboarding_service import onboard_user

# Asymmetric only: a token "signed" with a shared secret or with nothing must
# never verify against the provider's published keys.
ID_TOKEN_ALGORITHMS = [
    "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512",
]  # fmt: skip

LOGIN_CODE_TTL_SECONDS = 60
LOGIN_CODE_CACHE_PREFIX = "oidc-login-code:"


def oidc_enabled() -> bool:
    return bool(env.OIDC_SERVER_URL and env.OIDC_CLIENT_ID)


def frontend_callback_url(code: str) -> str:
    return f"{_frontend_url()}/sso/callback?code={code}"


def frontend_login_error_url(reason: str) -> str:
    return f"{_frontend_url()}/login?sso_error={reason}"


def _frontend_url() -> str:
    return (env.DARE_FRONTEND_URL or "").rstrip("/")


def verified_id_token_claims(id_token: str, openid_config: dict) -> dict:
    """The ID token's claims, once its signature, issuer and audience check out."""
    signing_key = jwt.PyJWKClient(openid_config["jwks_uri"]).get_signing_key_from_jwt(
        id_token
    )
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=ID_TOKEN_ALGORITHMS,
        audience=env.OIDC_CLIENT_ID,
        issuer=openid_config["issuer"],
    )


def email_domain_allowed(email: str) -> bool:
    allowed = [domain.lower() for domain in env.OIDC_ALLOWED_EMAIL_DOMAINS]
    return not allowed or email.rpartition("@")[2].lower() in allowed


def claimed_groups(claims: dict) -> list:
    groups = claims.get(env.OIDC_GROUPS_CLAIM) or []
    return [groups] if isinstance(groups, str) else list(groups)


def sync_platform_role(user, claims: dict) -> None:
    """With a role map configured, the provider's groups decide the role on every login."""
    if not env.OIDC_ROLE_MAP:
        return
    role = role_for_groups(claimed_groups(claims), env.OIDC_ROLE_MAP)
    if user.platform_role != role:
        user.platform_role = role
        user.save(update_fields=["platform_role"])


def onboard_sso_user(user, claims: dict) -> None:
    """A first sign-in joins the configured access-code group, then takes its role."""
    code_group = (
        AccessCodeGroup.objects.filter(access_code=env.OIDC_ACCESS_CODE).first()
        if env.OIDC_ACCESS_CODE
        else None
    )
    onboard_user(user, code_group)
    sync_platform_role(user, claims)


def issue_login_code(user) -> str:
    """A short-lived, single-use code the frontend trades for the user's tokens."""
    code = secrets.token_urlsafe(32)
    cache.set(LOGIN_CODE_CACHE_PREFIX + code, user.pk, LOGIN_CODE_TTL_SECONDS)
    return code


def redeem_login_code(code: str):
    """The user a code was issued for, or None. A code works once."""
    key = LOGIN_CODE_CACHE_PREFIX + code
    user_id = cache.get(key)
    # delete() reports whether this caller removed the key, so of two
    # concurrent redemptions only one gets the user.
    if user_id is None or not cache.delete(key):
        return None
    return get_user_model().objects.filter(pk=user_id, is_active=True).first()
