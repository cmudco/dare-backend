"""
Workflow node handlers package.

Each handler executes a specific type of workflow node.
EventEmitter centralizes WebSocket event dispatch.
"""
from workflows.handlers.base import (
    BaseNodeHandler,
    ExecutionNode,
    NodeExecutionContext,
    NodeExecutionResult,
    categorize_error,
)
from workflows.handlers.event_emitter import EventEmitter
from workflows.handlers.execution_base import BaseExecutionHandler
from workflows.handlers.base_routing_handler import BaseRoutingHandler
from workflows.handlers.start_handler import StartNodeHandler
from workflows.handlers.output_handler import OutputNodeHandler
from workflows.handlers.structured_output_handler import StructuredOutputNodeHandler
from workflows.handlers.step_handler import StepNodeHandler
from workflows.handlers.file_handler import FileNodeHandler
from workflows.handlers.registry import NodeHandlerRegistry, node_handler_registry

__all__ = [
    # Base classes and utilities
    'BaseNodeHandler',
    'BaseExecutionHandler',
    'BaseRoutingHandler',
    'ExecutionNode',
    'NodeExecutionContext',
    'NodeExecutionResult',
    'categorize_error',
    'EventEmitter',

    # Node handlers
    'StartNodeHandler',
    'OutputNodeHandler',
    'StructuredOutputNodeHandler',
    'StepNodeHandler',
    'FileNodeHandler',

    # Registry
    'NodeHandlerRegistry',
    'node_handler_registry',
]
