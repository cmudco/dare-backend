"""
Utility functions for workflows module.
"""
import re


def camel_to_snake(name: str) -> str:
    """
    Convert camelCase to snake_case.
    
    Examples:
        >>> camel_to_snake('stepNumber')
        'step_number'
        >>> camel_to_snake('maxContextSnippets')
        'max_context_snippets'
    """
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def convert_keys_to_snake_case(data: dict) -> dict:
    """
    Convert all dictionary keys from camelCase to snake_case.
    
    Why this is needed despite using djangorestframework-camelcase:
    ---------------------------------------------------------------
    The DRF camelcase library (djangorestframework-camelcase) automatically
    transforms request/response data between camelCase (frontend) and 
    snake_case (backend) at the parser/renderer level.
    
    However, this transformation does NOT apply to:
    1. Nested JSONField data - When a serializer field is a JSONField 
       (like our 'data' field in WorkflowNodeSerializer), DRF camelcase
       treats it as an opaque blob and doesn't transform its internal keys.
    2. Generic Foreign Key polymorphic data - Our WorkflowNode uses a
       GenericForeignKey to point to different data models (StepNodeData,
       StartNodeData, etc.). The nested 'data' dict must be manually
       converted before passing to the appropriate typed serializer.
    
    This function handles the manual conversion for these edge cases where
    the automatic camelcase middleware cannot reach.
    
    Args:
        data: Dictionary with potentially camelCase keys
        
    Returns:
        Dictionary with all keys converted to snake_case
        
    Examples:
        >>> convert_keys_to_snake_case({'stepNumber': 1, 'maxTokens': 100})
        {'step_number': 1, 'max_tokens': 100}
    """
    if not isinstance(data, dict):
        return data
    return {camel_to_snake(k): v for k, v in data.items()}
