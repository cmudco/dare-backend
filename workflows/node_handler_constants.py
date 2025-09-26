"""
Constants for workflow node handlers.

This file contains scoring thresholds and other configurable values
used in node execution logic.
"""


class ScoringThresholds:
    """Scoring thresholds for quantitative aggregator node evaluation."""
    QUANTITATIVE_BAD_MAX = 40
    QUANTITATIVE_AVERAGE_MAX = 70
    QUANTITATIVE_GOOD_MIN = 71


class DefaultValues:
    """Default values for workflow execution."""
    DEFAULT_LLM_PROVIDER = "openai"
    DEFAULT_TASK_MESSAGE = "Please proceed with the task."


class StepNodeDefaults:
    """Default values for StepNodeData model fields."""
    MAX_TOKENS = 2048
    TEMPERATURE = 0.7
    MAX_CONTEXT_SNIPPETS = 4
    DOCUMENT_SIMILARITY_THRESHOLD = 0.2