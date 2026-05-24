"""OAuth helpers for remote MCP servers."""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from django.core.cache import cache
from django.core.signing import BadSignature, Signer
from django.urls import reverse

from config import env as config_env
from mcp.models import MCPServer


OAUTH_STATE_CACHE_PREFIX = "mcp:oauth:state:"
OAUTH_STATE_TTL_SECONDS = 600


class MCPOAuthError(Exception):
    """Raised when a remote MCP OAuth step fails."""
    pass


@dataclass(frozen=True)
class MCPOAuthStart:
    """OAuth authorization URL and state returned to the frontend."""

    authorization_url: str
    state: str


@dataclass(frozen=True)
class MCPOAuthToken:
    """Token payload normalized for encrypted storage."""

    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str
    scope: str

    def to_credentials(self) -> dict[str, str]:
        credentials = {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "scope": self.scope,
        }
        if self.refresh_token:
            credentials["refresh_token"] = self.refresh_token
        return credentials

    def to_metadata(self) -> dict:
        expires_at = int(time.time()) + self.expires_in if self.expires_in else None
        return {
            "auth_type": "oauth2",
            "expires_at": expires_at,
            "scope": self.scope,
            "token_type": self.token_type,
        }


class MCPOAuthService:
    """Builds OAuth URLs and exchanges authorization codes for remote MCPs."""

    def __init__(self):
        self.signer = Signer()

    def build_authorization_url(self, server: MCPServer, user_id: int, request) -> MCPOAuthStart:
        redirect_uri = self.get_redirect_uri(request)
        self._ensure_client_id(server, redirect_uri)
        self._validate_oauth_server(server)
        code_verifier = self._generate_code_verifier()
        state = self.signer.sign(f"{user_id}:{server.slug}:{secrets.token_urlsafe(16)}")

        cache.set(
            self._state_cache_key(state),
            {
                "user_id": user_id,
                "server_slug": server.slug,
                "code_verifier": code_verifier,
            },
            OAUTH_STATE_TTL_SECONDS,
        )

        params = {
            "response_type": "code",
            "client_id": server.oauth_client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": self._code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        if server.oauth_scope:
            params["scope"] = server.oauth_scope

        return MCPOAuthStart(
            authorization_url=f"{server.oauth_authorize_url}?{urlencode(params)}",
            state=state,
        )

    def pop_state(self, state: str) -> dict:
        try:
            self.signer.unsign(state)
        except BadSignature as error:
            raise MCPOAuthError("Invalid OAuth state") from error

        cache_key = self._state_cache_key(state)
        cached = cache.get(cache_key)
        if not cached:
            raise MCPOAuthError("OAuth state expired or already used")

        cache.delete(cache_key)
        return cached

    async def exchange_code(self, server: MCPServer, code: str, code_verifier: str, request) -> MCPOAuthToken:
        self._validate_oauth_server(server)
        token_payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": server.oauth_client_id,
            "redirect_uri": self.get_redirect_uri(request),
            "code_verifier": code_verifier,
        }
        return await self._post_token_request(server.oauth_token_url, token_payload)

    async def refresh_access_token(self, server: MCPServer, refresh_token: str) -> MCPOAuthToken:
        self._validate_oauth_server(server)
        token_payload = {
            "grant_type": "refresh_token",
            "client_id": server.oauth_client_id,
            "refresh_token": refresh_token,
        }
        return await self._post_token_request(server.oauth_token_url, token_payload)

    def get_redirect_uri(self, request) -> str:
        callback_path = reverse("mcp:oauth-callback")
        backend_base = config_env.DARE_BACKEND_URL or ""
        if backend_base:
            return f"{backend_base.rstrip('/')}{callback_path}"
        return request.build_absolute_uri(callback_path)

    def get_frontend_redirect_url(self, server_slug: str, status: str, message: str = "") -> str:
        frontend_base = config_env.DARE_FRONTEND_URL or ""
        if not frontend_base:
            return ""
        params = urlencode({"server": server_slug, "status": status, "message": message})
        mcp_path = f"/mcp/{server_slug}" if server_slug else "/mcp"
        return f"{frontend_base.rstrip('/')}{mcp_path}?{params}"

    async def _post_token_request(self, url: str, payload: dict[str, str]) -> MCPOAuthToken:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.post(
                    url,
                    data=payload,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise MCPOAuthError(f"OAuth token request failed: {error}") from error

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise MCPOAuthError("OAuth token response did not include access_token")

        return MCPOAuthToken(
            access_token=access_token,
            refresh_token=data.get("refresh_token", ""),
            expires_in=int(data.get("expires_in") or 0),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope", ""),
        )

    def _validate_oauth_server(self, server: MCPServer):
        missing = []
        if not server.oauth_authorize_url:
            missing.append("oauth_authorize_url")
        if not server.oauth_token_url:
            missing.append("oauth_token_url")
        if not server.oauth_client_id:
            missing.append("oauth_client_id")
        if missing:
            raise MCPOAuthError(
                f"{server.name} is missing OAuth configuration: {', '.join(missing)}"
            )

    def _ensure_client_id(self, server: MCPServer, redirect_uri: str):
        if server.oauth_client_id:
            return
        if not server.oauth_registration_url:
            return

        payload = {
            "client_name": "DARE",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if server.oauth_scope:
            payload["scope"] = server.oauth_scope

        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.post(
                    server.oauth_registration_url,
                    json=payload,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise MCPOAuthError(f"OAuth client registration failed: {error}") from error

        data = response.json()
        client_id = data.get("client_id")
        if not client_id:
            raise MCPOAuthError("OAuth registration response did not include client_id")

        server.oauth_client_id = client_id
        server.save(update_fields=["oauth_client_id", "updated_at"])

    def _generate_code_verifier(self) -> str:
        return secrets.token_urlsafe(64)

    def _code_challenge(self, code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def _state_cache_key(self, state: str) -> str:
        return f"{OAUTH_STATE_CACHE_PREFIX}{state}"


mcp_oauth_service = MCPOAuthService()
