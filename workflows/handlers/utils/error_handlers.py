"""
Workflow handler error handling utilities.

This module provides sophisticated error handling with multi-layer extraction
and graceful degradation, following the pattern established in LLM provider
error handlers.
"""
import logging
from typing import Optional, Tuple, Dict, Any
from enum import Enum

from .constants import ErrorCode, ErrorMessage, MetadataKey


logger = logging.getLogger(__name__)


# ==================== Error Categories ====================

class ErrorCategory(Enum):
    """Error category enumeration for classification."""
    VALIDATION = "validation"
    SERVICE = "service"
    UNEXPECTED = "unexpected"
    CONFIGURATION = "configuration"
    DATABASE = "database"
    NETWORK = "network"


# ==================== Base Error Handler ====================

class BaseErrorHandler:
    """
    Base error handler with common extraction patterns.

    Provides multi-layer error extraction with graceful degradation,
    attempting multiple strategies to extract meaningful error information.
    """

    @staticmethod
    def check_for_timeout_error(error: Exception) -> bool:
        """
        Check if error is a timeout error.

        Attempts multiple extraction strategies:
        1. Check error body dictionary
        2. Check HTTP response
        3. Check string representation

        Args:
            error: The exception to check

        Returns:
            True if timeout error detected, False otherwise
        """
        # Step 1: Check error body
        try:
            body = getattr(error, "body", None)
            if isinstance(body, dict):
                err = body.get("error", {})
                if isinstance(err, dict):
                    error_type = err.get("type", "").lower()
                    if "timeout" in error_type:
                        return True
        except Exception:
            pass

        # Step 2: Check HTTP response
        try:
            resp = getattr(error, "response", None)
            if resp is not None:
                data = resp.json()
                if "timeout" in str(data).lower():
                    return True
        except Exception:
            pass

        # Step 3: Check string representation
        if "timeout" in str(error).lower():
            return True

        return False

    @staticmethod
    def check_for_overload_error(error: Exception) -> bool:
        """
        Check if error is due to service overload.

        Attempts multiple extraction strategies to detect overload conditions.

        Args:
            error: The exception to check

        Returns:
            True if overload error detected, False otherwise
        """
        # Step 1: Check error body
        try:
            body = getattr(error, "body", None)
            if isinstance(body, dict):
                err = body.get("error", {})
                if isinstance(err, dict):
                    error_type = err.get("type", "").lower()
                    if "overload" in error_type or "capacity" in error_type:
                        return True
        except Exception:
            pass

        # Step 2: Check HTTP response
        try:
            resp = getattr(error, "response", None)
            if resp is not None:
                if resp.status_code in [503, 429]:
                    return True
                data = resp.json()
                if "overload" in str(data).lower() or "rate limit" in str(data).lower():
                    return True
        except Exception:
            pass

        # Step 3: Check string representation
        error_str = str(error).lower()
        if any(keyword in error_str for keyword in ["overload", "rate limit", "capacity", "too many requests"]):
            return True

        return False

    @staticmethod
    def extract_error_message(error: Exception) -> str:
        """
        Extract a user-friendly error message from an exception.

        Uses multiple extraction strategies with fallback.

        Args:
            error: The exception to extract message from

        Returns:
            Extracted error message
        """
        # Priority 1: Check for specific error types
        if BaseErrorHandler.check_for_overload_error(error):
            return "Service is currently experiencing high traffic. Please try again in a moment."

        if BaseErrorHandler.check_for_timeout_error(error):
            return "Request timed out. Please try again."

        # Priority 2: Extract from error body
        try:
            body = getattr(error, "body", None)
            if isinstance(body, dict):
                err = body.get("error", {})
                if isinstance(err, dict):
                    message = err.get("message")
                    if message:
                        return str(message)
        except Exception:
            pass

        # Priority 3: Extract from response
        try:
            resp = getattr(error, "response", None)
            if resp is not None:
                data = resp.json()
                if isinstance(data, dict):
                    message = data.get("message") or data.get("error")
                    if message:
                        return str(message)
        except Exception:
            pass

        # Priority 4: Try message attribute
        try:
            if hasattr(error, "message") and error.message:
                return str(error.message)
        except Exception:
            pass

        # Priority 5: Fallback to string representation
        return str(error)


# ==================== Workflow-Specific Error Handlers ====================

class WorkflowErrorHandler:
    """
    Error handler for workflow execution errors.

    Provides workflow-specific error formatting and categorization.
    """

    @staticmethod
    def format_error(error: Exception, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Format an error for workflow execution context.

        Args:
            error: The exception to format
            context: Optional context dictionary with node_id, node_type, etc.

        Returns:
            Formatted user-friendly error message
        """
        base_message = BaseErrorHandler.extract_error_message(error)

        # Add context if provided
        if context:
            node_id = context.get("node_id")
            node_type = context.get("node_type")

            if node_id and node_type:
                return f"Error in {node_type} node '{node_id}': {base_message}"
            elif node_id:
                return f"Error in node '{node_id}': {base_message}"

        return base_message

    @staticmethod
    def categorize_error(error: Exception) -> Tuple[ErrorCategory, str]:
        """
        Categorize an exception for logging and metrics.

        Args:
            error: The exception to categorize

        Returns:
            Tuple of (ErrorCategory, error_type_name)
        """
        error_type = type(error).__name__

        # Validation errors
        if any(keyword in error_type.lower() for keyword in ["validation", "value", "type", "assertion"]):
            return ErrorCategory.VALIDATION, error_type

        # Database errors
        if any(keyword in error_type.lower() for keyword in ["database", "integrity", "operational"]):
            return ErrorCategory.DATABASE, error_type

        # Network/Service errors
        if any(keyword in error_type.lower() for keyword in ["connection", "timeout", "http", "request"]):
            return ErrorCategory.NETWORK, error_type

        # Configuration errors
        if any(keyword in error_type.lower() for keyword in ["config", "settings", "environment"]):
            return ErrorCategory.CONFIGURATION, error_type

        # Service-specific errors
        if BaseErrorHandler.check_for_overload_error(error) or BaseErrorHandler.check_for_timeout_error(error):
            return ErrorCategory.SERVICE, error_type

        # Default to unexpected
        return ErrorCategory.UNEXPECTED, error_type


class NodeHandlerErrorHandler:
    """
    Error handler for node handler execution errors.

    Provides node-specific error formatting with detailed context.
    """

    @staticmethod
    def format_validation_error(
        node_id: str,
        node_type: str,
        validation_error: str
    ) -> str:
        """
        Format a validation error message.

        Args:
            node_id: The node ID where validation failed
            node_type: The node type
            validation_error: The validation error description

        Returns:
            Formatted validation error message
        """
        return ErrorMessage.format(
            ErrorMessage.INVALID_NODE_DATA,
            expected_type=node_type,
            actual_type=validation_error
        )

    @staticmethod
    def format_llm_error(
        node_id: str,
        error: Exception
    ) -> str:
        """
        Format an LLM service error.

        Args:
            node_id: The node ID where LLM call failed
            error: The exception from LLM service

        Returns:
            Formatted LLM error message
        """
        error_message = BaseErrorHandler.extract_error_message(error)
        return ErrorMessage.format(
            ErrorMessage.LLM_CALL_FAILED,
            node_id=node_id,
            error=error_message
        )

    @staticmethod
    def format_routing_error(
        decision: str,
        available_routes: list
    ) -> str:
        """
        Format a routing decision error.

        Args:
            decision: The invalid routing decision
            available_routes: List of available routes

        Returns:
            Formatted routing error message
        """
        return ErrorMessage.format(
            ErrorMessage.INVALID_ROUTE,
            decision=decision,
            routes=", ".join(available_routes)
        )

    @staticmethod
    def format_input_error(node_id: str, reason: str) -> str:
        """
        Format an input error message.

        Args:
            node_id: The node ID where input error occurred
            reason: The reason for input error

        Returns:
            Formatted input error message
        """
        return ErrorMessage.format(
            ErrorMessage.MISSING_INPUT,
            node_id=node_id
        ) + f" Reason: {reason}"


# ==================== Error Result Builder ====================

class ErrorResultBuilder:
    """
    Builder for creating standardized error results.

    Provides consistent error result structure across handlers.
    """

    @staticmethod
    def build_error_result(
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        include_category: bool = True
    ) -> Dict[str, Any]:
        """
        Build a standardized error result dictionary.

        Args:
            error: The exception that occurred
            context: Optional context (node_id, node_type, etc.)
            include_category: Whether to include error category in metadata

        Returns:
            Dictionary with error result structure
        """
        error_message = WorkflowErrorHandler.format_error(error, context)
        category, error_type = WorkflowErrorHandler.categorize_error(error)

        result = {
            "success": False,
            "output": None,
            "error": error_message,
            "token_usage": None,
            "execution_time": None,
            "metadata": {}
        }

        if include_category:
            result["metadata"][MetadataKey.ERROR_CATEGORY] = category.value
            result["metadata"][MetadataKey.ERROR_TYPE] = error_type

        return result

    @staticmethod
    def build_validation_error_result(
        node_id: str,
        node_type: str,
        validation_message: str
    ) -> Dict[str, Any]:
        """
        Build a validation error result.

        Args:
            node_id: The node ID where validation failed
            node_type: The node type
            validation_message: The validation error message

        Returns:
            Dictionary with validation error result
        """
        error_message = NodeHandlerErrorHandler.format_validation_error(
            node_id, node_type, validation_message
        )

        return {
            "success": False,
            "output": None,
            "error": error_message,
            "token_usage": None,
            "execution_time": None,
            "metadata": {
                MetadataKey.ERROR_CATEGORY: ErrorCategory.VALIDATION.value,
                MetadataKey.ERROR_TYPE: "ValidationError"
            }
        }


# ==================== Retry Helper ====================

class RetryHelper:
    """
    Helper for determining if an error is retriable.

    Follows retry patterns from LLM provider implementations.
    """

    @staticmethod
    def is_retriable_error(error: Exception) -> bool:
        """
        Determine if an error is retriable.

        Args:
            error: The exception to check

        Returns:
            True if error is retriable, False otherwise
        """
        # Timeout and overload errors are retriable
        if BaseErrorHandler.check_for_timeout_error(error):
            return True

        if BaseErrorHandler.check_for_overload_error(error):
            return True

        # Check for specific HTTP status codes
        try:
            resp = getattr(error, "response", None)
            if resp is not None:
                if resp.status_code in [408, 429, 500, 502, 503, 504]:
                    return True
        except Exception:
            pass

        # Check error message for retriable patterns
        error_str = str(error).lower()
        retriable_patterns = [
            "timeout",
            "connection",
            "rate limit",
            "overload",
            "503",
            "502",
            "504",
            "temporary",
            "transient"
        ]

        return any(pattern in error_str for pattern in retriable_patterns)


# ==================== Export All ====================

__all__ = [
    "ErrorCategory",
    "BaseErrorHandler",
    "WorkflowErrorHandler",
    "NodeHandlerErrorHandler",
    "ErrorResultBuilder",
    "RetryHelper",
]
