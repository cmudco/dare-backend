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


# Redis cache keys and TTLs
TOOL_CACHE_KEY_PREFIX = 'mcp:tools:'
TOOL_CACHE_TTL = 3600  # 1 hour

CONNECTION_CACHE_KEY_PREFIX = 'mcp:connection:'
CONNECTION_CACHE_TTL = 300  # 5 minutes

# Subprocess timeouts
MCP_SUBPROCESS_TIMEOUT = 30  # seconds
MCP_IDLE_TIMEOUT = 300  # 5 minutes before killing idle subprocess

# Docker Configuration
# Import from config.env which properly reads the .env file
from config import env as config_env

MCP_USE_DOCKER = config_env.MCP_USE_DOCKER

# Docker image names for each MCP server (keyed by server slug)
MCP_DOCKER_IMAGES = {
    'slack': 'dare-mcp-slack:latest',
    'github': 'dare-mcp-github:latest',
}


