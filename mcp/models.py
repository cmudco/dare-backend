"""
MCP (Model Context Protocol) models.

Models:
- MCPServer: Admin-configured available MCP servers
- UserMCPConnection: User's credentials for an MCP server
- MCPToolExecution: Audit trail for tool calls
- GatewayFetch: Full responses served through the MCP gateway (the corpus)
"""

from django.conf import settings
from django.db import models

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from mcp.constants import ExecutionStatus, MCPAuthType, MCPTransport


class MCPServer(BaseModel):
    """
    MCP servers available on the platform (seeded by admin).

    Each server represents an MCP-compatible service that can be connected to
    via subprocess (npx in dev, Docker in prod).
    """

    name = models.CharField(
        max_length=100, help_text="Display name for this server (e.g., 'Slack')"
    )
    slug = models.SlugField(
        unique=True, help_text="URL-friendly identifier (e.g., 'slack')"
    )
    description = models.TextField(
        blank=True, help_text="Description of what this server provides"
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Icon identifier for frontend (e.g., 'slack', 'github')",
    )

    transport = models.CharField(
        max_length=32,
        choices=MCPTransport.choices(),
        default=MCPTransport.STDIO,
        help_text="How DARE connects to this MCP server",
    )

    auth_type = models.CharField(
        max_length=32,
        choices=MCPAuthType.choices(),
        default=MCPAuthType.CREDENTIALS,
        help_text="How users authenticate to this MCP server",
    )

    # Docker configuration (used when MCP_USE_DOCKER=True)
    docker_image = models.CharField(
        max_length=255,
        blank=True,
        help_text="Docker image to use in production (e.g., 'dare-mcp-slack:latest')",
    )

    # Runtime configuration (used when MCP_USE_DOCKER=False)
    command = models.CharField(
        max_length=255, help_text="Command to run in dev mode (e.g., 'npx')"
    )
    args = models.JSONField(
        default=list,
        help_text="Command arguments as JSON array (e.g., ['-y', '@modelcontextprotocol/server-slack'])",
    )

    remote_url = models.URLField(
        blank=True, help_text="Remote MCP endpoint for streamable HTTP servers"
    )
    remote_headers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Non-secret headers to send to remote MCP servers",
    )

    oauth_authorize_url = models.URLField(
        blank=True, help_text="OAuth authorization endpoint for remote MCP servers"
    )
    oauth_token_url = models.URLField(
        blank=True, help_text="OAuth token and refresh endpoint for remote MCP servers"
    )
    oauth_registration_url = models.URLField(
        blank=True,
        help_text="OAuth dynamic client registration endpoint, when supported",
    )
    oauth_scope = models.CharField(
        max_length=255, blank=True, help_text="OAuth scopes to request"
    )
    oauth_client_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="OAuth client ID if dynamic registration is not used",
    )

    # Credential schema - what credentials does this server need?
    required_credentials = models.JSONField(
        default=list,
        help_text="""
        Schema for required credentials as JSON array. Example:
        [
            {"key": "SLACK_BOT_TOKEN", "label": "Bot Token", "type": "password", "placeholder": "xoxb-...", "required": true},
            {"key": "SLACK_TEAM_ID", "label": "Team ID", "type": "text", "required": false}
        ]
        """,
    )

    # Help URL for users to get credentials
    credentials_help_url = models.URLField(
        blank=True,
        help_text="URL to documentation/guide on how to get credentials for this server",
    )

    # Additional environment variables (admin-configured, not user-provided)
    extra_env_vars = models.JSONField(
        default=dict,
        blank=True,
        help_text="""
        Additional environment variables to set when spawning this server. Example:
        {"SLACK_MCP_ADD_MESSAGE_TOOL": "true", "SLACK_MCP_DEBUG": "1"}
        These are admin-configured, not user-provided.
        """,
    )

    # In-app setup guide (markdown)
    setup_guide = models.TextField(
        blank=True,
        help_text="Markdown-formatted setup instructions shown in the connection modal",
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "MCP Server"
        verbose_name_plural = "MCP Servers"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.slug})"


class UserMCPConnection(BaseModel):
    """
    User's connection to an MCP server with their credentials.

    Credentials are stored encrypted using AES-256 via the credential service.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_connections",
        help_text="User who owns this connection",
    )
    server = models.ForeignKey(
        MCPServer,
        on_delete=models.CASCADE,
        related_name="user_connections",
        help_text="MCP server this connection is for",
    )

    # Encrypted credentials stored as JSON: {"SLACK_BOT_TOKEN": "encrypted_value", ...}
    encrypted_credentials = models.JSONField(
        default=dict,
        help_text="Encrypted credentials as JSON object (keys are credential names, values are encrypted)",
    )

    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time this connection was used to call a tool",
    )

    # Optional: cache tools in DB as fallback if Redis unavailable
    cached_tools = models.JSONField(
        default=list, blank=True, help_text="Cached tool definitions as fallback"
    )
    tools_cached_at = models.DateTimeField(
        null=True, blank=True, help_text="When tools were last cached"
    )
    auth_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Non-secret connection metadata such as OAuth expiry and account identity",
    )

    all_objects = models.Manager()  # Default manager for get_or_create, etc.
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "User MCP Connection"
        verbose_name_plural = "User MCP Connections"
        unique_together = ["user", "server"]
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "server"]),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.server.name}"


class MCPToolExecution(BaseModel):
    """
    Records each MCP tool call execution for audit/history.

    Links to Message/Conversation when called from LLM context.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_tool_executions",
        help_text="User who executed this tool",
    )
    server = models.ForeignKey(
        MCPServer,
        on_delete=models.CASCADE,
        related_name="tool_executions",
        help_text="MCP server the tool belongs to",
    )

    # Optional links to conversation context (for LLM integration - Phase 2)
    message = models.ForeignKey(
        "conversations.Message",
        on_delete=models.CASCADE,
        related_name="mcp_tool_executions",
        null=True,
        blank=True,
        help_text="Message that triggered this tool call (if from LLM)",
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="mcp_tool_executions",
        null=True,
        blank=True,
        help_text="Conversation context (if from LLM)",
    )

    # Tool call details
    tool_name = models.CharField(
        max_length=255,
        help_text="Name of the tool that was called (e.g., 'send_message')",
    )
    tool_arguments = models.JSONField(
        default=dict, help_text="Arguments passed to the tool"
    )

    # Execution result
    status = models.CharField(
        max_length=20,
        choices=ExecutionStatus.choices(),
        default=ExecutionStatus.PENDING,
        help_text="Execution status",
    )
    result = models.JSONField(null=True, blank=True, help_text="Tool response/result")
    error_message = models.TextField(
        blank=True, help_text="Error message if execution failed"
    )

    # Performance tracking
    execution_time_ms = models.PositiveIntegerField(
        null=True, blank=True, help_text="Execution time in milliseconds"
    )

    all_objects = models.Manager()  # Default manager for create, etc.
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "MCP Tool Execution"
        verbose_name_plural = "MCP Tool Executions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "server"]),
            models.Index(fields=["status"]),
            models.Index(fields=["tool_name"]),
        ]

    def __str__(self):
        return f"{self.tool_name} ({self.status}) - {self.user.email}"


class GatewayFetch(BaseModel):
    """
    The complete response of a tool call served through the MCP gateway — the
    corpus of everything a delegated agent actually read. Page fetches are
    deduped by URL (a refetch updates the row); search responses are one row
    per call. Downstream consumers: audit ("what did the agent read?"),
    citation-edge derivation from scholarly responses, and grounding chat in
    already-fetched full texts.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gateway_fetches",
        help_text="User whose gateway served this call",
    )
    tool = models.CharField(
        max_length=255,
        help_text="Gateway tool name (e.g. 'fetch_page', 'scite__search_literature')",
    )
    url = models.URLField(
        max_length=1000,
        blank=True,
        help_text="Fetched page URL (blank for search-style tools)",
    )
    doi = models.CharField(
        max_length=255,
        blank=True,
        help_text="DOI extracted from a doi.org URL, when present",
    )
    arguments = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arguments the tool was called with",
    )
    content = models.TextField(
        help_text="Complete, untrimmed response text",
    )
    error = models.TextField(
        blank=True,
        default="",
        help_text="Failure reason when the gateway call errored (blank on success)",
    )
    run_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Hermes session id that made this call (DARE's "
            "'<hermes_session_id>-r<run_id>'), when the agent runtime forwards "
            "it — lets the run audit attribute each row to its exact run instead "
            "of matching by time window. Blank when not forwarded."
        ),
    )

    all_objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "Gateway Fetch"
        verbose_name_plural = "Gateway Fetches"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "tool"]),
            models.Index(fields=["doi"]),
        ]

    def __str__(self):
        target = self.url or self.doi or self.tool
        return f"{self.tool}: {target}"
