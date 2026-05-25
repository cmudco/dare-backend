"""
Credential service for MCP connections.

Handles encryption, decryption, and masking of MCP credentials
using the existing AES-256 encryption utilities.
"""

from core.utils.encryption import encrypt_value, decrypt_value


class MCPCredentialService:
    """
    Service for managing MCP credentials.
    
    Uses AES-256 encryption via core.utils.encryption.
    """

    @staticmethod
    def encrypt_credentials(credentials: dict) -> dict:
        """
        Encrypt each credential value.
        
        Args:
            credentials: Dict of credential key -> plaintext value
            Example: {"SLACK_BOT_TOKEN": "xoxb-12345"}
        
        Returns:
            Dict of credential key -> encrypted value
            Example: {"SLACK_BOT_TOKEN": "base64_encrypted_string"}
        """
        if not credentials:
            return {}

        encrypted = {}
        for key, value in credentials.items():
            if value:
                encrypted[key] = encrypt_value(str(value))
            else:
                encrypted[key] = ""
        return encrypted

    @staticmethod
    def decrypt_credentials(encrypted_credentials: dict) -> dict:
        """
        Decrypt credentials for use in subprocess environment variables.
        
        Args:
            encrypted_credentials: Dict of credential key -> encrypted value
        
        Returns:
            Dict of credential key -> plaintext value (for env vars)
        """
        if not encrypted_credentials:
            return {}

        decrypted = {}
        for key, value in encrypted_credentials.items():
            if value:
                decrypted[key] = decrypt_value(value)
            else:
                decrypted[key] = ""
        return decrypted

    @staticmethod
    def mask_credentials(credentials: dict) -> dict:
        """
        Return masked version of credentials for API responses.
        
        Shows first 4 and last 4 characters with asterisks in between.
        
        Args:
            credentials: Dict of credential key -> plaintext value
        
        Returns:
            Dict of credential key -> masked value
            Example: {"SLACK_BOT_TOKEN": "xoxb****5678"}
        """
        if not credentials:
            return {}

        masked = {}
        for key, value in credentials.items():
            if value:
                masked[key] = MCPCredentialService._mask_value(str(value))
            else:
                masked[key] = None
        return masked

    @staticmethod
    def _mask_value(value: str) -> str:
        """
        Mask a single credential value.
        
        Logic:
        - If length <= 8: show first 2 and last 2
        - Otherwise: show first 4 and last 4
        """
        if not value:
            return ""

        length = len(value)

        if length <= 4:
            return '*' * length
        elif length <= 8:
            return f"{value[:2]}{'*' * (length - 4)}{value[-2:]}"
        else:
            return f"{value[:4]}{'*' * (length - 8)}{value[-4:]}"

    @staticmethod
    def validate_credentials_schema(credentials: dict, required_credentials: list) -> tuple[bool, list[str]]:
        """
        Validate that all required credentials are provided.
        
        Args:
            credentials: User-provided credentials dict
            required_credentials: Schema from MCPServer.required_credentials
        
        Returns:
            Tuple of (is_valid, list of missing required fields)
        """
        missing = []

        for cred_schema in required_credentials:
            key = cred_schema.get('key')
            required = cred_schema.get('required', True)

            if required and (not credentials or not credentials.get(key)):
                missing.append(cred_schema.get('label', key))

        return (len(missing) == 0, missing)

    @staticmethod
    def get_access_token(credentials: dict) -> str:
        """Resolve the access token key used by bearer and OAuth connections."""
        return (
            credentials.get("access_token")
            or credentials.get("ACCESS_TOKEN")
            or credentials.get("bearer_token")
            or credentials.get("BEARER_TOKEN")
            or credentials.get("api_key")
            or credentials.get("API_KEY")
            or ""
        )

    @staticmethod
    def get_refresh_token(credentials: dict) -> str:
        """Resolve the refresh token key used by OAuth connections."""
        return credentials.get("refresh_token") or credentials.get("REFRESH_TOKEN") or ""
