"""
Node Data Handler - extracted from WorkflowNodeSerializer for better organization.
Contains existing data normalization logic without modifications.
"""

class NodeDataHandler:
    """
    Handler for normalizing React Flow node data to Django model format.

    This class provides centralized logic for converting between camelCase (React Flow)
    and snake_case (Django) field names for different node types. Extracted from
    WorkflowNodeSerializer to improve maintainability and testability.
    """

    @classmethod
    def normalize_data(cls, node_type: str, data: dict) -> dict:
        """
        Normalize node data keys based on node_type.

        Converts camelCase field names from React Flow frontend to snake_case
        field names expected by Django models. Handles type-specific field
        mappings for step, chatOutput, and aggregator nodes.

        Args:
            node_type: The type of node (step, start, chatOutput, aggregator)
            data: Raw data dictionary from React Flow frontend

        Returns:
            dict: Normalized node data with snake_case keys

        Note:
            Extracted from WorkflowNodeSerializer.to_internal_value()
        """
        node_data = (data.get('data') or {}).copy()

        if node_type == 'step':
            nd_map = {
                'contentFiles': 'content_files',
                'embeddingFiles': 'embedding_files',
                'stepNumber': 'step_number',
                'maxTokens': 'max_tokens',
                'maxContextSnippets': 'max_context_snippets',
                'documentSimilarityThreshold': 'document_similarity_threshold',
                'usePreviousStepFiles': 'use_previous_step_files',
                'usePreviousStepEmbeddings': 'use_previous_step_embeddings',
            }
            for ck, sk in nd_map.items():
                if ck in node_data and sk not in node_data:
                    node_data[sk] = node_data.pop(ck)
        elif node_type == 'chatOutput':
            if 'stepNumber' in node_data and 'step_number' not in node_data:
                node_data['step_number'] = node_data.pop('stepNumber')
        elif node_type == 'aggregator':
            nd_map = {
                'scoringMode': 'scoring_mode',
                'customPrompt': 'custom_prompt',
                'stepNumber': 'step_number',
            }
            for ck, sk in nd_map.items():
                if ck in node_data and sk not in node_data:
                    node_data[sk] = node_data.pop(ck)

        return node_data