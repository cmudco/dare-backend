"""
Constants for MCP app.
"""


class ExecutionStatus:
    """Status choices for MCPToolExecution"""
    PENDING = 'pending'
    SUCCESS = 'success'
    ERROR = 'error'

    @classmethod
    def choices(cls):
        return [
            (cls.PENDING, 'Pending'),
            (cls.SUCCESS, 'Success'),
            (cls.ERROR, 'Error'),
        ]


class MCPTransport:
    """Supported MCP transport modes."""
    STDIO = 'stdio'
    STREAMABLE_HTTP = 'streamable_http'

    @classmethod
    def choices(cls):
        return [
            (cls.STDIO, 'Stdio'),
            (cls.STREAMABLE_HTTP, 'Streamable HTTP'),
        ]


class MCPAuthType:
    """Supported authentication models for MCP servers."""
    CREDENTIALS = 'credentials'
    NONE = 'none'
    BEARER = 'bearer'
    OAUTH2 = 'oauth2'

    @classmethod
    def choices(cls):
        return [
            (cls.CREDENTIALS, 'Credentials'),
            (cls.NONE, 'None'),
            (cls.BEARER, 'Bearer token'),
            (cls.OAUTH2, 'OAuth 2.0'),
        ]


# Redis cache keys and TTLs
TOOL_CACHE_KEY_PREFIX = 'mcp:tools:'
TOOL_CACHE_TTL = 3600  # 1 hour

CONNECTION_CACHE_KEY_PREFIX = 'mcp:connection:'
CONNECTION_CACHE_TTL = 300  # 5 minutes

# Subprocess timeouts
MCP_SUBPROCESS_TIMEOUT = 30  # seconds
MCP_REMOTE_REQUEST_TIMEOUT = 30.0  # seconds for remote setup/discovery calls
MCP_REMOTE_TOOL_CALL_TIMEOUT = 120.0  # hosted searches can take longer
MCP_IDLE_TIMEOUT = 300  # 5 minutes before killing idle subprocess

# Docker Configuration
# Import from config.env which properly reads the .env file
from config import env as config_env

MCP_USE_DOCKER = config_env.MCP_USE_DOCKER
