"""
Workflow handler utilities package.

This package provides modular, reusable utilities for workflow handlers
following the patterns established in LLM provider utilities.

Modules:
    constants: Centralized constants and configuration values
    error_handlers: Multi-layer error extraction and handling
    validation_helpers: Common validation patterns
    message_preparers: Message building and formatting
    llm_executors: Standardized LLM execution patterns
    route_resolvers: Route resolution and normalization

Design Principles:
    - Composition over inheritance
    - Defensive programming with graceful degradation
    - Comprehensive type hints and documentation
    - Reusable, testable components
    - Consistent error handling patterns
"""

# ==================== Constants ====================
from .constants import (
    NodeType,
    EdgeHandle,
    XMLTag,
    LLMDefaults,
    WorkflowStatus,
    StepStatus,
    ErrorCode,
    MetadataKey,
    SkipReason,
    ErrorMessage,
    PromptTemplate,
    FileProcessing,
    RetryConfig,
    LogConfig,
    ValidationRule,
)

# ==================== Error Handlers ====================
from .error_handlers import (
    ErrorCategory,
    BaseErrorHandler,
    WorkflowErrorHandler,
    NodeHandlerErrorHandler,
    ErrorResultBuilder,
    RetryHelper,
)

# ==================== Validation Helpers ====================
from .validation_helpers import (
    MetadataValidator,
    NodeDataValidator,
    InputValidator,
    LLMConfigValidator,
    RouteValidator,
)
from .execution_validator import ExecutionValidator

# ==================== Message Preparers ====================
from .message_preparers import (
    MessagePreparer,
    StepMessagePreparer,
    StructuredOutputMessagePreparer,
    FileContextPreparer,
)

# ==================== LLM Executors ====================
from .llm_executors import (
    LLMConfig,
    LLMExecutor,
    ResponseAggregator,
    LLMSelector,
)

# ==================== Route Resolvers ====================
from .route_resolvers import (
    RouteResolver,
    RouteNormalizer,
    StructuredOutputBuilder,
    RouteInstructionBuilder,
)


# ==================== Public API ====================

__all__ = [
    # Constants
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
    # Error Handlers
    "ErrorCategory",
    "BaseErrorHandler",
    "WorkflowErrorHandler",
    "NodeHandlerErrorHandler",
    "ErrorResultBuilder",
    "RetryHelper",
    # Validation Helpers
    "MetadataValidator",
    "NodeDataValidator",
    "InputValidator",
    "LLMConfigValidator",
    "RouteValidator",
    "ExecutionValidator",
    # Message Preparers
    "MessagePreparer",
    "StepMessagePreparer",
    "StructuredOutputMessagePreparer",
    "FileContextPreparer",
    # LLM Executors
    "LLMConfig",
    "LLMExecutor",
    "ResponseAggregator",
    "LLMSelector",
    # Route Resolvers
    "RouteResolver",
    "RouteNormalizer",
    "StructuredOutputBuilder",
    "RouteInstructionBuilder",
]


# ==================== Version Info ====================

__version__ = "1.0.0"
__author__ = "DARE Development Team"
__description__ = "Workflow handler utilities following LLM provider patterns"
