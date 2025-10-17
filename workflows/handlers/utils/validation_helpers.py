"""
Workflow handler validation utilities.

This module provides common validation patterns used across handlers,
following defensive programming practices from LLM provider implementations.
"""
import logging
from typing import Dict, Any, Optional, List, Type, TypeVar
from dataclasses import is_dataclass

from .constants import (
    ValidationRule,
    MetadataKey,
    NodeType,
    ErrorMessage
)


logger = logging.getLogger(__name__)

T = TypeVar('T')


# ==================== Metadata Validation ====================

class MetadataValidator:
    """
    Validator for metadata dictionaries.

    Provides safe access patterns with type checking and defaults.
    """

    @staticmethod
    def is_skipped(metadata: Optional[Dict[str, Any]]) -> bool:
        """
        Check if a result is marked as skipped.

        Args:
            metadata: The metadata dictionary to check

        Returns:
            True if skipped, False otherwise
        """
        if metadata is None:
            return False
        return metadata.get(MetadataKey.SKIPPED, False)

    @staticmethod
    def has_routing_decision(metadata: Optional[Dict[str, Any]]) -> bool:
        """
        Check if metadata contains a routing decision.

        Args:
            metadata: The metadata dictionary to check

        Returns:
            True if routing decision exists, False otherwise
        """
        if metadata is None:
            return False
        return MetadataKey.ROUTING_DECISION in metadata

    @staticmethod
    def get_routing_decision(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        Safely extract routing decision from metadata.

        Args:
            metadata: The metadata dictionary

        Returns:
            Routing decision string or None if not found
        """
        if metadata is None:
            return None
        return metadata.get(MetadataKey.ROUTING_DECISION)

    @staticmethod
    def is_pending_human_validation(metadata: Optional[Dict[str, Any]]) -> bool:
        """
        Check if result is pending human validation.

        Args:
            metadata: The metadata dictionary to check

        Returns:
            True if pending human validation, False otherwise
        """
        if metadata is None:
            return False
        return metadata.get(MetadataKey.PENDING_HUMAN_VALIDATION, False)

    @staticmethod
    def is_human_validated(metadata: Optional[Dict[str, Any]]) -> bool:
        """
        Check if result has been human validated.

        Args:
            metadata: The metadata dictionary to check

        Returns:
            True if human validated, False otherwise
        """
        if metadata is None:
            return False
        return metadata.get(MetadataKey.IS_HUMAN_VALIDATED, False)

    @staticmethod
    def get_safe(
        metadata: Optional[Dict[str, Any]],
        key: str,
        default: Any = None,
        expected_type: Optional[Type] = None
    ) -> Any:
        """
        Safely get a value from metadata with type checking.

        Args:
            metadata: The metadata dictionary
            key: The key to retrieve
            default: Default value if key not found
            expected_type: Optional type to validate against

        Returns:
            The value or default if not found/invalid type
        """
        if metadata is None:
            return default

        value = metadata.get(key, default)

        if expected_type is not None and value is not None:
            if not isinstance(value, expected_type):
                logger.warning(
                    f"Metadata key '{key}' has unexpected type: "
                    f"expected {expected_type.__name__}, got {type(value).__name__}"
                )
                return default

        return value


# ==================== Node Data Validation ====================

class NodeDataValidator:
    """
    Validator for node data objects.

    Provides type checking and validation for database node data.
    """

    @staticmethod
    def validate_node_data_type(
        node_data: Any,
        expected_type: Type[T],
        node_id: str
    ) -> bool:
        """
        Validate that node data matches expected type.

        Args:
            node_data: The node data object to validate
            expected_type: The expected type class
            node_id: Node ID for error messages

        Returns:
            True if valid, False otherwise
        """
        if node_data is None:
            logger.error(f"Node data is None for node {node_id}")
            return False

        if not isinstance(node_data, expected_type):
            logger.error(
                f"Invalid node data type for node {node_id}: "
                f"expected {expected_type.__name__}, got {type(node_data).__name__}"
            )
            return False

        return True

    @staticmethod
    def validate_required_fields(
        data_object: Any,
        required_fields: List[str],
        node_id: str
    ) -> tuple[bool, Optional[str]]:
        """
        Validate that data object has all required fields.

        Args:
            data_object: The data object to validate
            required_fields: List of required field names
            node_id: Node ID for error messages

        Returns:
            Tuple of (is_valid, error_message)
        """
        for field in required_fields:
            if not hasattr(data_object, field):
                error_msg = f"Node {node_id} missing required field: {field}"
                logger.error(error_msg)
                return False, error_msg

            value = getattr(data_object, field)
            if value is None:
                error_msg = f"Node {node_id} has None value for required field: {field}"
                logger.warning(error_msg)
                # Don't fail, just warn for None values

        return True, None


# ==================== Input Validation ====================

class InputValidator:
    """
    Validator for node inputs.

    Provides validation for various input types and formats.
    """

    @staticmethod
    def validate_single_input(
        previous_results: Dict[str, Dict],
        node_id: str,
        node_type: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Validate that node has exactly one input.

        Args:
            previous_results: Dictionary of previous node results
            node_id: Current node ID
            node_type: Current node type

        Returns:
            Tuple of (is_valid, error_message, input_value)
        """
        non_skipped_results = [
            (nid, result) for nid, result in previous_results.items()
            if not MetadataValidator.is_skipped(result.get('metadata'))
        ]

        if len(non_skipped_results) == 0:
            error_msg = ErrorMessage.format(
                ErrorMessage.MISSING_INPUT,
                node_id=node_id
            )
            return False, error_msg, None

        if len(non_skipped_results) > 1:
            error_msg = ErrorMessage.format(
                ErrorMessage.AMBIGUOUS_INPUT,
                node_type=node_type
            )
            return False, error_msg, None

        input_value = non_skipped_results[0][1].get('output')
        return True, None, input_value

    @staticmethod
    def has_valid_input(
        previous_results: Dict[str, Dict],
        current_input: Optional[str]
    ) -> bool:
        """
        Check if there is any valid input available.

        Args:
            previous_results: Dictionary of previous node results
            current_input: Current input value

        Returns:
            True if valid input exists, False otherwise
        """
        # Check current_input first
        if current_input:
            return True

        # Check previous results
        if not previous_results:
            return False

        non_skipped_results = [
            result for result in previous_results.values()
            if not MetadataValidator.is_skipped(result.get('metadata'))
        ]

        return len(non_skipped_results) > 0

    @staticmethod
    def get_input_from_results(
        previous_results: Dict[str, Dict],
        prefer_latest: bool = True
    ) -> Optional[str]:
        """
        Extract input value from previous results.

        Args:
            previous_results: Dictionary of previous node results
            prefer_latest: If True, returns latest result; if False, returns first

        Returns:
            Input string or None if not found
        """
        if not previous_results:
            return None

        non_skipped_results = [
            result for result in previous_results.values()
            if not MetadataValidator.is_skipped(result.get('metadata'))
        ]

        if not non_skipped_results:
            return None

        # Get first or last based on preference
        result = non_skipped_results[-1] if prefer_latest else non_skipped_results[0]
        return result.get('output')


# ==================== LLM Configuration Validation ====================

class LLMConfigValidator:
    """
    Validator for LLM configuration parameters.

    Ensures LLM parameters are within acceptable ranges.
    """

    @staticmethod
    def validate_temperature(temperature: float) -> tuple[bool, Optional[str]]:
        """
        Validate temperature parameter.

        Args:
            temperature: The temperature value to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(temperature, (int, float)):
            return False, f"Temperature must be numeric, got {type(temperature).__name__}"

        if temperature < ValidationRule.MIN_TEMPERATURE or temperature > ValidationRule.MAX_TEMPERATURE:
            return False, (
                f"Temperature must be between {ValidationRule.MIN_TEMPERATURE} "
                f"and {ValidationRule.MAX_TEMPERATURE}, got {temperature}"
            )

        return True, None

    @staticmethod
    def validate_max_tokens(max_tokens: int) -> tuple[bool, Optional[str]]:
        """
        Validate max_tokens parameter.

        Args:
            max_tokens: The max tokens value to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(max_tokens, int):
            return False, f"Max tokens must be integer, got {type(max_tokens).__name__}"

        if max_tokens < ValidationRule.MIN_MAX_TOKENS or max_tokens > ValidationRule.MAX_MAX_TOKENS:
            return False, (
                f"Max tokens must be between {ValidationRule.MIN_MAX_TOKENS} "
                f"and {ValidationRule.MAX_MAX_TOKENS}, got {max_tokens}"
            )

        return True, None

    @staticmethod
    def validate_llm_config(
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> tuple[bool, List[str]]:
        """
        Validate complete LLM configuration.

        Args:
            temperature: Optional temperature to validate
            max_tokens: Optional max tokens to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        if temperature is not None:
            is_valid, error = LLMConfigValidator.validate_temperature(temperature)
            if not is_valid:
                errors.append(error)

        if max_tokens is not None:
            is_valid, error = LLMConfigValidator.validate_max_tokens(max_tokens)
            if not is_valid:
                errors.append(error)

        return len(errors) == 0, errors


# ==================== Route Validation ====================

class RouteValidator:
    """
    Validator for routing configuration and decisions.

    Validates route names and routing decisions.
    """

    @staticmethod
    def validate_route_name(route_name: str) -> tuple[bool, Optional[str]]:
        """
        Validate a route name.

        Args:
            route_name: The route name to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(route_name, str):
            return False, f"Route name must be string, got {type(route_name).__name__}"

        if len(route_name) < ValidationRule.MIN_ROUTE_NAME_LENGTH:
            return False, f"Route name must be at least {ValidationRule.MIN_ROUTE_NAME_LENGTH} character"

        if len(route_name) > ValidationRule.MAX_ROUTE_NAME_LENGTH:
            return False, f"Route name exceeds maximum length of {ValidationRule.MAX_ROUTE_NAME_LENGTH}"

        return True, None

    @staticmethod
    def validate_routes_list(routes: List[str]) -> tuple[bool, List[str]]:
        """
        Validate a list of route names.

        Args:
            routes: List of route names to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        if not isinstance(routes, list):
            return False, [f"Routes must be a list, got {type(routes).__name__}"]

        if len(routes) == 0:
            return False, ["Routes list cannot be empty"]

        errors = []
        for route in routes:
            is_valid, error = RouteValidator.validate_route_name(route)
            if not is_valid:
                errors.append(f"Invalid route '{route}': {error}")

        return len(errors) == 0, errors

    @staticmethod
    def is_valid_route_decision(
        decision: str,
        available_routes: List[str],
        case_sensitive: bool = False
    ) -> bool:
        """
        Check if a routing decision is valid.

        Args:
            decision: The routing decision to check
            available_routes: List of available routes
            case_sensitive: Whether to use case-sensitive matching

        Returns:
            True if valid, False otherwise
        """
        if not decision:
            return False

        if case_sensitive:
            return decision in available_routes

        decision_lower = decision.lower()
        routes_lower = [r.lower() for r in available_routes]
        return decision_lower in routes_lower


# ==================== Export All ====================

__all__ = [
    "MetadataValidator",
    "NodeDataValidator",
    "InputValidator",
    "LLMConfigValidator",
    "RouteValidator",
]
