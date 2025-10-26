"""
Workflow node handlers package.

This package contains all node type handlers for workflow execution.
Each handler is responsible for executing a specific type of workflow node.
"""
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
