"""
Node type handlers for workflow execution.

This module provides backward compatibility by re-exporting all handlers
from the new modular structure in workflows/handlers/.

The handlers have been refactored into separate files for better maintainability:
- workflows/handlers/base.py - Base classes and utilities
- workflows/handlers/start_handler.py - StartNodeHandler
- workflows/handlers/output_handler.py - OutputNodeHandler
- workflows/handlers/conditional_handler.py - ConditionalNodeHandler
- workflows/handlers/step_handler.py - StepNodeHandler
- workflows/handlers/structured_output_handler.py - StructuredOutputHandler
- workflows/handlers/registry.py - NodeHandlerRegistry

All imports from this module will continue to work as before.
"""

# Re-export all classes for backward compatibility
from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)

from workflows.handlers.start_handler import StartNodeHandler
from workflows.handlers.output_handler import OutputNodeHandler
from workflows.handlers.conditional_handler import ConditionalNodeHandler
from workflows.handlers.step_handler import StepNodeHandler
from workflows.handlers.registry import NodeHandlerRegistry, node_handler_registry

__all__ = [
    # Base classes and utilities
    'BaseNodeHandler',
    'ExecutionNode',
    'NodeExecutionContext',
    'NodeExecutionResult',
    'categorize_error',

    # Node handlers
    'StartNodeHandler',
    'OutputNodeHandler',
    'ConditionalNodeHandler',
    'StepNodeHandler',

    # Registry
    'NodeHandlerRegistry',
    'node_handler_registry',
]
