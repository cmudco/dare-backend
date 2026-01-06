"""
Workflow handler constants and configuration values.

This module centralizes all magic strings, default values, and configuration
constants used across workflow handlers, following the pattern established
in the LLM provider utilities.
"""
import re
from typing import Dict, Any


# ==================== Node Types ====================

class NodeType:
    """Node type constants."""
    START = "start"
    STEP = "step"
    CHAT_OUTPUT = "chatOutput"
    STRUCTURED_OUTPUT = "structuredOutput"


# ==================== Edge Handle Formats ====================

class EdgeHandle:
    """Edge handle format constants for routing."""
    OUTPUT_PREFIX = "output-"

    @staticmethod
    def format_output_handle(route_value: str) -> str:
        """Format an output handle for a given route value."""
        return f"{EdgeHandle.OUTPUT_PREFIX}{route_value}"

    @staticmethod
    def extract_route_from_handle(handle: str) -> str:
        """Extract route value from an output handle."""
        if handle.startswith(EdgeHandle.OUTPUT_PREFIX):
            return handle[len(EdgeHandle.OUTPUT_PREFIX):]
        return handle


# ==================== XML Tags for Parsing ====================

class XMLTag:
    """XML tag constants for LLM response parsing."""
    DECISION = "decision"
    ANALYSIS = "analysis"

    @staticmethod
    def extract_tag_content(xml_string: str, tag_name: str) -> str:
        """
        Extract content from XML tag.

        Args:
            xml_string: The XML string to parse
            tag_name: The tag name to extract (without brackets)

        Returns:
            Extracted content or empty string if not found
        """
        pattern = f"<{tag_name}>(.*?)</{tag_name}>"
        match = re.search(pattern, xml_string, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""


# ==================== LLM Configuration Defaults ====================

class LLMDefaults:
    """Default LLM configuration values."""

    # Step node defaults
    STEP_TEMPERATURE = 0.7
    STEP_MAX_TOKENS = 1024

    # Structured output node defaults
    STRUCTURED_OUTPUT_TEMPERATURE = 0.1
    STRUCTURED_OUTPUT_MAX_TOKENS = 100

    # Fallback LLM provider
    DEFAULT_PROVIDER = "openai"
    DEFAULT_MODEL = "gpt-4"


# ==================== Workflow Status Constants ====================

class WorkflowStatus:
    """Workflow run status constants."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING_HUMAN_INPUT = "pending_human_input"


# ==================== Step Status Constants ====================

class StepStatus:
    """Workflow run step status constants."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    PENDING_HUMAN_INPUT = "pending_human_input"


# ==================== Special Error Codes ====================

class ErrorCode:
    """Special error codes for handler communication."""
    PENDING_HUMAN_INPUT = "PENDING_HUMAN_INPUT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SERVICE_ERROR = "SERVICE_ERROR"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


# ==================== Metadata Keys ====================

class MetadataKey:
    """Standard metadata dictionary keys."""
    SKIPPED = "skipped"
    REASON = "reason"
    ROUTING_DECISION = "routing_decision"
    IS_HUMAN_VALIDATED = "is_human_validated"
    PENDING_HUMAN_VALIDATION = "pending_human_validation"
    USER_CHOICE = "user_choice"
    AI_RECOMMENDATION = "ai_recommendation"
    AI_ANALYSIS = "ai_analysis"
    ANALYSIS = "analysis"  # General analysis text (AI reasoning)
    AVAILABLE_ROUTES = "available_routes"
    RAW_RESPONSE = "raw_response"
    SELECTED_ROUTE = "selected_route"
    USE_STRUCTURED_OUTPUT_NODE = "use_structured_output_node"
    EXECUTION_TIME = "execution_time"
    TOKEN_USAGE = "token_usage"
    ERROR_CATEGORY = "error_category"
    ERROR_TYPE = "error_type"


# ==================== Skip Reasons ====================

class SkipReason:
    """Reasons for skipping node execution."""
    ROUTING_DECISION = "routing_decision"
    MISSING_DEPENDENCY = "missing_dependency"
    INVALID_CONFIGURATION = "invalid_configuration"


# ==================== Error Messages ====================

class ErrorMessage:
    """Standard error message templates."""

    # Node data errors
    INVALID_NODE_DATA = "Invalid node data: expected {expected_type}, got {actual_type}"
    MISSING_NODE_DATA = "No data found for {node_type} node: {node_id}"

    # Input errors
    MISSING_INPUT = "No input available for node: {node_id}"
    AMBIGUOUS_INPUT = "Multiple inputs found for {node_type} node. Only single input supported."
    INVALID_INPUT_TYPE = "Invalid input type: expected {expected_type}, got {actual_type}"

    # Routing errors
    INVALID_ROUTE = "Invalid routing decision '{decision}'. Available routes: {routes}"
    NO_ROUTES_AVAILABLE = "No routes configured for structured output node: {node_id}"
    ROUTE_RESOLUTION_FAILED = "Failed to resolve route from LLM response: {response}"

    # LLM errors
    LLM_CALL_FAILED = "LLM call failed for node {node_id}: {error}"
    LLM_NOT_CONFIGURED = "No LLM configured for step node: {node_id}"

    # Configuration errors
    MISSING_CONFIGURATION = "Missing required configuration: {config_name}"
    INVALID_CONFIGURATION = "Invalid configuration for {config_name}: {reason}"

    # Database errors
    DATABASE_ERROR = "Database operation failed: {operation}"

    @staticmethod
    def format(template: str, **kwargs) -> str:
        """Format an error message template with provided values."""
        return template.format(**kwargs)


# ==================== Prompt Templates ====================

class PromptTemplate:
    """Prompt templates for LLM interactions."""

    STRUCTURED_OUTPUT_INSTRUCTION = """Please respond with EXACTLY one of these values (no additional text):
{route_values}

If uncertain, default to: {default_route}"""


# ==================== File Processing Constants ====================

class FileProcessing:
    """File processing configuration constants."""
    DEFAULT_SIMILARITY_THRESHOLD = 0.7
    DEFAULT_CONTEXT_SNIPPET_COUNT = 3
    MAX_FILE_SIZE_MB = 50
    SUPPORTED_CONTENT_TYPES = [
        "text/plain",
        "text/markdown",
        "application/pdf",
        "application/json",
    ]


# ==================== Retry Configuration ====================

class RetryConfig:
    """Retry configuration for transient failures."""
    MAX_RETRIES = 3
    INITIAL_BACKOFF_SECONDS = 1
    MAX_BACKOFF_SECONDS = 30
    BACKOFF_MULTIPLIER = 2

    # Retriable error patterns
    RETRIABLE_ERROR_PATTERNS = [
        "timeout",
        "connection",
        "rate limit",
        "overload",
        "503",
        "502",
        "504",
    ]


# ==================== Logging Configuration ====================

class LogConfig:
    """Logging configuration constants."""
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(correlation_id)s - %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    # Log level thresholds
    ERROR_THRESHOLD_SECONDS = 10  # Log error if execution takes longer
    WARNING_THRESHOLD_SECONDS = 5  # Log warning if execution takes longer


# ==================== Validation Rules ====================

class ValidationRule:
    """Validation rule constants."""
    MIN_TEMPERATURE = 0.0
    MAX_TEMPERATURE = 2.0
    MIN_MAX_TOKENS = 1
    MAX_MAX_TOKENS = 100000
    MIN_ROUTE_NAME_LENGTH = 1
    MAX_ROUTE_NAME_LENGTH = 100


# ==================== Export All ====================

__all__ = [
    "NodeType",
    "EdgeHandle",
    "XMLTag",
    "LLMDefaults",
    "WorkflowStatus",
    "StepStatus",
    "ErrorCode",
    "MetadataKey",
    "SkipReason",
    "ErrorMessage",
    "PromptTemplate",
    "FileProcessing",
    "RetryConfig",
    "LogConfig",
    "ValidationRule",
]
